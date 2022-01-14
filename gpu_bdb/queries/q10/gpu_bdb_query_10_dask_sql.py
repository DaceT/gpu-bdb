#
# Copyright (c) 2019-2022, NVIDIA CORPORATION.
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

import dask.dataframe as dask_cudf

from bdb_tools.cluster_startup import attach_to_cluster

from bdb_tools.utils import (
    benchmark,
    gpubdb_argparser,
    run_query,
)

from bdb_tools.text import (
    create_sentences_from_reviews,
    create_words_from_sentences
)

from bdb_tools.readers import build_reader

from dask.distributed import wait


eol_char = "è"


def read_tables(data_dir, c, config):

    ### splitting by row groups for better parallelism
    table_reader = build_reader(
        data_format=config["file_format"],
        basepath=config["data_dir"],
        split_row_groups=True,
    )
    product_reviews_cols = ["pr_item_sk", "pr_review_content", "pr_review_sk"]

    product_reviews_df = table_reader.read(
        "product_reviews", relevant_cols=product_reviews_cols,
    )

    c.create_table("product_reviews", product_reviews_df, persist=False)


def main(data_dir, client, c, config):
    benchmark(read_tables, data_dir, c, config, dask_profile=config["dask_profile"])

    query_1 = """
        SELECT pr_item_sk,
            pr_review_content,
            pr_review_sk
        FROM product_reviews
        where pr_review_content IS NOT NULL
        
    """
<<<<<<< HEAD
    product_reviews_df = c.sql(query_1)

=======
    product_reviews_df = bc.sql(query_1)
    
   
>>>>>>> e643645ea033098affa0dbe8fec8de71ddf94fb3
    product_reviews_df[
        "pr_review_content"
    ] = product_reviews_df.pr_review_content.str.lower()
    
    product_reviews_df[
        "pr_review_content"
    ] = product_reviews_df.pr_review_content.str.replace(
        '\.|\?|!', eol_char, regex=True 
    )
    
    sentences = product_reviews_df.map_partitions(create_sentences_from_reviews)
    
    product_reviews_df = product_reviews_df[["pr_item_sk", "pr_review_sk"]]
    product_reviews_df["pr_review_sk"] = product_reviews_df["pr_review_sk"].astype("int32")
    
    # need the global position in the sentence tokenized df
    sentences["x"] = 1
    sentences["sentence_tokenized_global_pos"] = sentences.x.cumsum()
    del sentences["x"]

    word_df = sentences.map_partitions(
        create_words_from_sentences,
        global_position_column="sentence_tokenized_global_pos",
    )
 
   
    product_reviews_df = product_reviews_df.persist()
    
    wait(product_reviews_df)
<<<<<<< HEAD
    c.create_table('product_reviews_df', product_reviews_df, persist=False)
    
=======
    bc.create_table('product_reviews_df', product_reviews_df, persist=False)

>>>>>>> e643645ea033098affa0dbe8fec8de71ddf94fb3
    sentences = sentences.persist()
    wait(sentences)
    c.create_table('sentences', sentences, persist=False)

    # These files come from the official TPCx-BB kit
    # We extracted them from bigbenchqueriesmr.jar
    # Need to pass the absolute path for these txt files
    sentiment_dir = os.path.join(config["data_dir"], "sentiment_files")
<<<<<<< HEAD
    ns_df = dask_cudf.read_csv(os.path.join(sentiment_dir, "negativeSentiment.txt"), names=["sentiment_word"], persist=False)
    c.create_table('negative_sentiment', ns_df, persist=False)
    ps_df = dask_cudf.read_csv(os.path.join(sentiment_dir, "positiveSentiment.txt"), names=["sentiment_word"], persist=False)
    c.create_table('positive_sentiment', ps_df, persist=False)

    word_df = word_df.persist()
    wait(word_df)
    c.create_table('word_df', word_df, persist=False)

=======
    ns_df = dask_cudf.read_csv(os.path.join(sentiment_dir, "negativeSentiment.txt"), names=["sentiment_word"])
    bc.create_table('negative_sentiment', ns_df, persist=False)
    ps_df = dask_cudf.read_csv(os.path.join(sentiment_dir, "positiveSentiment.txt"), names=["sentiment_word"])
    bc.create_table('positive_sentiment', ps_df, persist=False)

    word_df = word_df.persist()
    wait(word_df)
    bc.create_table('word_df', word_df, persist=False)
   
>>>>>>> e643645ea033098affa0dbe8fec8de71ddf94fb3
    query = '''
        SELECT pr_item_sk as item_sk,
            sentence as review_sentence,
            sentiment,
            sentiment_word FROM
        (
            SELECT review_idx_global_pos,
                sentiment_word,
                sentiment,
                sentence FROM
            (
                WITH sent_df AS
                (
                    (SELECT sentiment_word, 'POS' as sentiment
                        FROM positive_sentiment
                        GROUP BY sentiment_word)
                    UNION ALL
                    (SELECT sentiment_word, 'NEG' as sentiment
                        FROM negative_sentiment
                        GROUP BY sentiment_word)
                )
                SELECT * FROM word_df
                INNER JOIN sent_df
                ON word_df.word = sent_df.sentiment_word
            ) word_sentence_sentiment
            LEFT JOIN sentences
            ON word_sentence_sentiment.sentence_idx_global_pos = sentences.sentence_tokenized_global_pos
        ) temp
        INNER JOIN product_reviews_df
        ON temp.review_idx_global_pos = product_reviews_df.pr_review_sk
        ORDER BY item_sk, review_sentence, sentiment, sentiment_word
    '''
    result = c.sql(query)

    c.drop_table("product_reviews_df")
    del product_reviews_df
    c.drop_table("sentences")
    del sentences
    c.drop_table("word_df")
    del word_df
   
    return result


if __name__ == "__main__":
    config = gpubdb_argparser()
    client, c = attach_to_cluster(config, create_sql_context=True)
    run_query(config=config, client=client, query_func=main, sql_context=c)
