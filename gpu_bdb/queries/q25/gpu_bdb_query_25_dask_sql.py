#
# Copyright (c) 2019-2020, NVIDIA CORPORATION.
# Copyright (c) 2019-2020, BlazingSQL, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import sys
import os

from bdb_tools.cluster_startup import attach_to_cluster

from bdb_tools.utils import (
    benchmark,
    gpubdb_argparser,
    run_query,
    train_clustering_model
)
from dask import delayed

from bdb_tools.readers import build_reader


# -------- Q25 -----------
# -- store_sales and web_sales date
q25_date = "2002-01-02"

N_CLUSTERS = 8
CLUSTER_ITERATIONS = 20
N_ITER = 5


def get_clusters(client, ml_input_df):
    import dask.dataframe as dask_cudf
    import pandas as pd

    ml_tasks = [
        delayed(train_clustering_model)(df, N_CLUSTERS, CLUSTER_ITERATIONS, N_ITER)
        for df in ml_input_df.to_delayed()
    ]
    results_dict = client.compute(*ml_tasks, sync=True)
#     print(len(results_dict))
#     print(results_dict)
    
    
#     s = pd.Series(results_dict)
#     s1 = s.map(s)
#     print(s1)
#     df = pd.DataFrame.from_dict(results_dict)
#     print(df.dtypes)
#     ddf = dask_cudf.from_pandas(df, npartitions=1)
    
    
#     r_dict =  pd.DataFrame(results_dict)
#     print(type(r_dict))
#     print(type(results_dict["cid_labels"]))
    
    output = ml_input_df.index.to_frame().reset_index(drop=True)
    
    
    labels_final = dask_cudf.from_pandas(
        pd.Series(results_dict["cid_labels"]), npartitions=output.npartitions
    )
    output["label"] = labels_final.reset_index()[0]

    # Based on CDH6.1 q25-result formatting
    results_dict["cid_labels"] = output
    return results_dict


def read_tables(data_dir, bc, config):
    table_reader = build_reader(
        data_format=config["file_format"],
        basepath=config["data_dir"],
        split_row_groups=config["split_row_groups"],
    )

    ss_cols = ["ss_customer_sk", "ss_sold_date_sk", "ss_ticket_number", "ss_net_paid"]
    ws_cols = [
        "ws_bill_customer_sk",
        "ws_sold_date_sk",
        "ws_order_number",
        "ws_net_paid",
    ]
    datedim_cols = ["d_date_sk", "d_date"]

    ss_ddf = table_reader.read("store_sales", relevant_cols=ss_cols, index=False)
    ws_ddf = table_reader.read("web_sales", relevant_cols=ws_cols, index=False)
    datedim_ddf = table_reader.read("date_dim", relevant_cols=datedim_cols, index=False)

    bc.create_table("web_sales", ws_ddf, persist=False)
    bc.create_table("store_sales", ss_ddf, persist=False)
    bc.create_table("date_dim", datedim_ddf, persist=False)


def agg_count_distinct(df, group_key, counted_key):
    """Returns a Series that is the result of counting distinct instances of 'counted_key' within each 'group_key'.
    The series' index will have one entry per unique 'group_key' value.
    Workaround for lack of nunique aggregate function on Dask df.
    """

    ### going via repartition for split_out drop duplicates
    unique_df = df[[group_key, counted_key]].map_partitions(
        lambda df: df.drop_duplicates()
    )
    unique_df = unique_df.shuffle(on=[group_key])
    unique_df = unique_df.map_partitions(lambda df: df.drop_duplicates())

    unique_df = unique_df.groupby(group_key)[counted_key].count()
    return unique_df.reset_index(drop=False)

def main(data_dir, client, bc, config):
    benchmark(read_tables, data_dir, bc, config, dask_profile=config["dask_profile"])

    q25_date = "2002-01-02"
    ss_join_query= f"""
        SELECT
            ss_customer_sk,
            ss_sold_date_sk,
            ss_net_paid,
            ss_ticket_number
        FROM
            store_sales ss
        JOIN
            date_dim d ON ss.ss_sold_date_sk = d.d_date_sk
        WHERE
            CAST(d.d_date AS DATE) > DATE '{q25_date}'
        AND
            ss_customer_sk IS NOT NULL
    """


    ws_join_query = f"""
        SELECT
            ws_bill_customer_sk,
            ws_order_number,
            ws_sold_date_sk,
            ws_net_paid
        FROM
            web_sales ws
        JOIN
            date_dim d ON ws.ws_sold_date_sk = d.d_date_sk
        WHERE
            CAST(d.d_date AS DATE) > DATE '{q25_date}'
        AND
            ws_bill_customer_sk IS NOT NULL
    """

    ss_merged_df = bc.sql(ss_join_query)
    ws_merged_df = bc.sql(ws_join_query)

    bc.create_table('ss_merged_table', ss_merged_df, persist=False)
    bc.create_table('ws_merged_table', ws_merged_df, persist=False)

    ss_agg_query = """
        SELECT
            ss_customer_sk AS cid,
            -- count(distinct ss_ticket_number) AS frequency,  # distinct count groupby OOMS with dask-sql
            max(ss_sold_date_sk) AS most_recent_date,
            CAST( SUM(ss_net_paid) AS DOUBLE) AS amount
        FROM ss_merged_table
        GROUP BY ss_customer_sk
        """
    ws_agg_query= """
        SELECT
            ws_bill_customer_sk AS cid,
            -- count(distinct ws_order_number) AS frequency, # distinct count groupby OOMS with dask-sql
            max(ws_sold_date_sk)   AS most_recent_date,
            CAST( SUM(ws_net_paid) AS DOUBLE) AS amount
        FROM ws_merged_table
        GROUP BY ws_bill_customer_sk
        """

    ss_distinct_count_agg = agg_count_distinct(ss_merged_df,'ss_customer_sk','ss_ticket_number')
    ss_distinct_count_agg = ss_distinct_count_agg.rename(columns={'ss_customer_sk':'cid',
                                                                  'ss_ticket_number':'frequency'})
    ss_agg_df = bc.sql(ss_agg_query)
    ### add distinct count
    ss_agg_df = ss_agg_df.merge(ss_distinct_count_agg)

    ws_distinct_count_agg =  agg_count_distinct(ws_merged_df,'ws_bill_customer_sk','ws_order_number')
    ws_distinct_count_agg =  ws_distinct_count_agg.rename(columns={'ws_bill_customer_sk':'cid',
                                                                   'ws_order_number':'frequency'})
    ws_agg_df = bc.sql(ws_agg_query)
    ### add distinct count
    ws_agg_df = ws_agg_df.merge(ws_distinct_count_agg)

    bc.create_table('ss_agg_df', ss_agg_df, persist=False)
    bc.create_table('ws_agg_df', ws_agg_df, persist=False)


    result_query = '''
            WITH  concat_table AS
            (
            SELECT * FROM ss_agg_df
            UNION ALL
            SELECT * FROM ws_agg_df
            )
            SELECT
                cid AS cid,
                CASE WHEN 37621 - max(most_recent_date) < 60 THEN 1.0
                    ELSE 0.0 END AS recency, -- 37621 == 2003-01-02
                CAST( SUM(frequency) AS BIGINT) AS frequency, --total frequency
                CAST( SUM(amount) AS DOUBLE)    AS amount --total amount
            FROM concat_table
            GROUP BY cid
            ORDER BY cid
            '''
    cluster_input_ddf = bc.sql(result_query)

    # Prepare df for KMeans clustering
    cluster_input_ddf["recency"] = cluster_input_ddf["recency"].astype("int64")

    cluster_input_ddf = cluster_input_ddf.repartition(npartitions=1)
    cluster_input_ddf = cluster_input_ddf.persist()
    cluster_input_ddf = cluster_input_ddf.set_index('cid')
    results_dict = get_clusters(client=client, ml_input_df=cluster_input_ddf)

    return results_dict


if __name__ == "__main__":
    config = gpubdb_argparser()
    client, bc = attach_to_cluster(config)
    run_query(config=config, client=client, query_func=main, blazing_context=bc)
