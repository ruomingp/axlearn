# Copyright © 2023 Apple Inc.

"""Tests dual-encoder modules."""
# pylint: disable=no-self-use
from typing import Optional, Tuple

import jax
import jax.numpy as jnp
import pytest

from axlearn.common.bert import bert_embedding_config, bert_model_config, bert_transformer_config
from axlearn.common.layers import Linear, RedirectToSharedModule
from axlearn.common.module import functional as F
from axlearn.common.test_utils import TestCase
from axlearn.common.text_dual_encoder import (
    NEGATIVE_EMBEDDINGS,
    NEGATIVE_INPUT_IDS,
    NEGATIVE_PADDINGS,
    NUM_VALID_RANKING_PAIRS,
    PAIRWISE_LOSS_EMBEDDINGS,
    PAIRWISE_LOSS_PADDINGS,
    POSITIVE_EMBEDDINGS,
    POSITIVE_INPUT_IDS,
    POSITIVE_PADDINGS,
    RANKS,
    SIMILARITY_MATRIX,
    TEXT_DUAL_ENCODER_SHARED_MODULE_NAME,
    RankingPairwiseLossLayer,
    TextEmbeddingAsymmetricContrastiveLossLayer,
    TextEmbeddingDualEncoder,
    TextEmbeddingStreamEncoder,
)
from axlearn.common.text_encoder import TextEmbeddingEncoder
from axlearn.common.utils import Tensor

HIDDEN_DIM = 16
VOCAB_SIZE = 32
LEFT_ENCODER_NAME = "query_encoder"
RIGHT_ENCODER_NAME = "doc_encoder"


def sample_text_embedding_stream_encoder_config(
    output_dim: int,
    output_proj: Linear.Config = None,
    hidden_dim: Optional[int] = None,
) -> TextEmbeddingStreamEncoder.Config:
    return TextEmbeddingStreamEncoder.default_config().set(
        text_encoder=TextEmbeddingEncoder.default_config().set(
            encoder=bert_model_config(
                vocab_size=VOCAB_SIZE,
                dropout_rate=0.1,
                embedding_cfg=bert_embedding_config(
                    max_position_embeddings=4,
                    type_vocab_size=2,
                    layer_norm_epsilon=1e-12,
                ),
                stack_cfg=bert_transformer_config(
                    num_layers=2,
                    num_heads=2,
                    layer_norm_epsilon=1e-12,
                ),
            ).encoder,
            pad_token_id=0,
        ),
        output_dim=output_dim,
        output_proj=output_proj,
        hidden_dim=hidden_dim,
    )


def sample_contrastive_loss_layer_config() -> TextEmbeddingAsymmetricContrastiveLossLayer.Config:
    return TextEmbeddingAsymmetricContrastiveLossLayer.default_config().set(
        left_encoder_name=LEFT_ENCODER_NAME,
        right_encoder_name=RIGHT_ENCODER_NAME,
    )


def sample_ranking_pairwise_loss_layer_config() -> RankingPairwiseLossLayer.Config:
    return RankingPairwiseLossLayer.default_config().set(
        left_encoder_name=LEFT_ENCODER_NAME,
        right_encoder_name=RIGHT_ENCODER_NAME,
        pairwise_loss_scale_factor=2.0,
    )


def random_int_array(*, shape: Tuple) -> Tensor:
    return jax.random.randint(jax.random.PRNGKey(0), shape=shape, minval=1, maxval=VOCAB_SIZE)


class TestTextEmbeddingStreamEncoder(TestCase):
    """Tests TextEmbeddingStreamEncoder."""

    def test_positive_inputs_only(self):
        model_cfg = sample_text_embedding_stream_encoder_config(output_dim=HIDDEN_DIM)
        model_cfg.set(name="test_positive_inputs_only")
        model = model_cfg.instantiate(parent=None)
        model_params = model.initialize_parameters_recursively(jax.random.PRNGKey(0))

        input_batch = {
            POSITIVE_INPUT_IDS: random_int_array(shape=(2, 2, 4)),
        }

        outputs, _ = F(
            model,
            inputs=dict(input_batch=input_batch),
            state=model_params,
            is_training=True,
            prng_key=jax.random.PRNGKey(0),
        )
        assert outputs[POSITIVE_EMBEDDINGS].shape == (2, 2, HIDDEN_DIM)

    def test_positive_inputs_only_with_projection(self):
        model_cfg = sample_text_embedding_stream_encoder_config(
            output_dim=8,
            output_proj=Linear.default_config(),
            hidden_dim=HIDDEN_DIM,
        )
        model_cfg.set(name="test_positive_inputs_only_with_projection")
        model = model_cfg.instantiate(parent=None)
        model_params = model.initialize_parameters_recursively(jax.random.PRNGKey(0))

        input_batch = {
            POSITIVE_INPUT_IDS: random_int_array(shape=(2, 2, 4)),
        }

        outputs, _ = F(
            model,
            inputs=dict(input_batch=input_batch),
            state=model_params,
            is_training=True,
            prng_key=jax.random.PRNGKey(0),
        )
        assert outputs[POSITIVE_EMBEDDINGS].shape == (2, 2, 8)

    def test_positive_and_negative_inputs(self):
        model_cfg = sample_text_embedding_stream_encoder_config(
            output_dim=HIDDEN_DIM,
        )
        model_cfg.set(name="test_positive_and_negative_inputs")
        model = model_cfg.instantiate(parent=None)
        model_params = model.initialize_parameters_recursively(jax.random.PRNGKey(0))

        input_batch = {
            POSITIVE_INPUT_IDS: random_int_array(shape=(2, 2, 4)),
            NEGATIVE_INPUT_IDS: random_int_array(shape=(2, 3, 4)),
        }

        outputs, _ = F(
            model,
            inputs=dict(input_batch=input_batch),
            state=model_params,
            is_training=True,
            prng_key=jax.random.PRNGKey(0),
        )
        assert outputs[POSITIVE_EMBEDDINGS].shape == (2, 2, HIDDEN_DIM)
        assert outputs[NEGATIVE_EMBEDDINGS].shape == (2, 3, HIDDEN_DIM)

    def test_missing_output_proj(self):
        model_cfg = sample_text_embedding_stream_encoder_config(
            output_dim=HIDDEN_DIM,
            hidden_dim=4,
        )
        model_cfg.set(name="test_missing_output_proj")
        with pytest.raises(
            AssertionError,
            match="output_proj can't be None when hidden_dim != output_dim",
        ):
            model_cfg.instantiate(parent=None)


class TestTextEmbeddingAsymmetricContrastiveLossLayer(TestCase):
    """Tests TextEmbeddingAsymmetricContrastiveLossLayer."""

    def test_no_negative_embeddings(self):
        model_cfg = sample_contrastive_loss_layer_config()
        model_cfg.set(name="test_no_negative_embeddings")
        model = model_cfg.instantiate(parent=None)
        model_params = model.initialize_parameters_recursively(jax.random.PRNGKey(0))

        input_batch = {
            LEFT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: random_int_array(shape=(2, 1, HIDDEN_DIM)),
            },
            RIGHT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: random_int_array(shape=(2, 1, HIDDEN_DIM)),
                POSITIVE_PADDINGS: jnp.zeros((2, 1)),
            },
        }

        outputs, _ = F(
            model,
            inputs=dict(input_batch=input_batch),
            state=model_params,
            is_training=True,
            prng_key=jax.random.PRNGKey(0),
        )
        assert outputs[1][SIMILARITY_MATRIX].shape == (2, 2)

    def test_positive_and_negative_embeddings(self):
        model_cfg = sample_contrastive_loss_layer_config()
        model_cfg.set(name="test_positive_and_negative_embeddings")
        model = model_cfg.instantiate(parent=None)
        model_params = model.initialize_parameters_recursively(jax.random.PRNGKey(0))

        input_batch = {
            LEFT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: random_int_array(shape=(2, 1, HIDDEN_DIM)),
            },
            RIGHT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: random_int_array(shape=(2, 1, HIDDEN_DIM)),
                POSITIVE_PADDINGS: jnp.zeros((2, 1)),
                NEGATIVE_EMBEDDINGS: random_int_array(shape=(4, 2, HIDDEN_DIM)),
                NEGATIVE_PADDINGS: jnp.asarray([[0, 0], [0, 1], [1, 1], [0, 1]]),
            },
        }

        outputs, _ = F(
            model,
            inputs=dict(input_batch=input_batch),
            state=model_params,
            is_training=True,
            prng_key=jax.random.PRNGKey(0),
        )
        assert outputs[1][SIMILARITY_MATRIX].shape == (2, 10)

    def test_shape_assertion(self):
        model_cfg = sample_contrastive_loss_layer_config()
        model_cfg.set(name="test_shape_assertion")
        model = model_cfg.instantiate(parent=None)
        model_params = model.initialize_parameters_recursively(jax.random.PRNGKey(0))

        input_batch = {
            LEFT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: random_int_array(shape=(4, 2, HIDDEN_DIM)),
            },
            RIGHT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: random_int_array(shape=(4, 1, HIDDEN_DIM)),
                POSITIVE_PADDINGS: jnp.zeros((4, 1)),
            },
        }
        with pytest.raises(
            AssertionError, match="one positive embedding per example from left encoder"
        ):
            F(
                model,
                inputs=dict(input_batch=input_batch),
                state=model_params,
                is_training=True,
                prng_key=jax.random.PRNGKey(0),
            )
        input_batch = {
            LEFT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: random_int_array(shape=(4, 1, HIDDEN_DIM)),
            },
            RIGHT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: random_int_array(shape=(4, 2, HIDDEN_DIM)),
                POSITIVE_PADDINGS: jnp.zeros((4, 1)),
            },
        }
        with pytest.raises(
            AssertionError, match="one positive embedding per example from right encoder"
        ):
            F(
                model,
                inputs=dict(input_batch=input_batch),
                state=model_params,
                is_training=True,
                prng_key=jax.random.PRNGKey(0),
            )
        input_batch = {
            LEFT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: random_int_array(shape=(4, 1, HIDDEN_DIM)),
            },
            RIGHT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: random_int_array(shape=(4, 1, HIDDEN_DIM * 2)),
                POSITIVE_PADDINGS: jnp.zeros((4, 1)),
            },
        }
        with pytest.raises(
            AssertionError,
            match="right_positive_embeddings has a different dim than that of left_embeddings",
        ):
            F(
                model,
                inputs=dict(input_batch=input_batch),
                state=model_params,
                is_training=True,
                prng_key=jax.random.PRNGKey(0),
            )
        input_batch = {
            LEFT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: random_int_array(shape=(4, 1, HIDDEN_DIM)),
            },
            RIGHT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: random_int_array(shape=(4, 1, HIDDEN_DIM)),
                POSITIVE_PADDINGS: jnp.zeros((4, 2)),
            },
        }
        with pytest.raises(
            AssertionError, match="one positive embedding per example from right encoder"
        ):
            F(
                model,
                inputs=dict(input_batch=input_batch),
                state=model_params,
                is_training=True,
                prng_key=jax.random.PRNGKey(0),
            )
        input_batch = {
            LEFT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: random_int_array(shape=(4, 1, HIDDEN_DIM)),
            },
            RIGHT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: random_int_array(shape=(4, 1, HIDDEN_DIM)),
                POSITIVE_PADDINGS: jnp.zeros((4, 1)),
                NEGATIVE_EMBEDDINGS: random_int_array(shape=(4, 1, HIDDEN_DIM * 2)),
                NEGATIVE_PADDINGS: jnp.zeros((4, 1)),
            },
        }
        with pytest.raises(
            AssertionError,
            match="right_negative_embeddings has a different dim than that of left_embeddings",
        ):
            F(
                model,
                inputs=dict(input_batch=input_batch),
                state=model_params,
                is_training=True,
                prng_key=jax.random.PRNGKey(0),
            )


class TestRankingPairwiseLossLayer(TestCase):
    """Tests RankingPairwiseLossLayer."""

    def test_ranking_pairwise_loss(self):
        model_cfg = sample_ranking_pairwise_loss_layer_config()
        model_cfg.set(name="test_pairwise_loss")
        model = model_cfg.instantiate(parent=None)
        model_params = model.initialize_parameters_recursively(jax.random.PRNGKey(0))

        input_batch = {
            LEFT_ENCODER_NAME: {
                POSITIVE_EMBEDDINGS: jnp.asarray([[[1, 0]]] * 3),
            },
            RIGHT_ENCODER_NAME: {
                PAIRWISE_LOSS_EMBEDDINGS: jnp.asarray(
                    [[[1, 0], [2, 0], [2, 0]], [[4, 0], [6, 0], [7, 0]], [[1, 0], [5, 0], [10, 0]]]
                ),
                PAIRWISE_LOSS_PADDINGS: jnp.asarray([[0, 1, 0], [0, 0, 0], [0, 0, 0]]),
                RANKS: jnp.asarray([[1, 2, 2], [1, 2, 3], [1, 4, 7]]),
            },
        }

        outputs, _ = F(
            model,
            inputs=dict(input_batch=input_batch),
            state=model_params,
            is_training=True,
            prng_key=jax.random.PRNGKey(0),
        )
        loss, aux = outputs
        assert aux[NUM_VALID_RANKING_PAIRS] == 7
        expected_logits_delta = (
            jnp.asarray([2 - 1, 6 - 4, 7 - 4, 7 - 6, 5 - 1, 10 - 1, 10 - 5])
            * model_cfg.pairwise_loss_scale_factor
        )
        expected_loss = jnp.mean(jnp.log(1 + jnp.exp(expected_logits_delta)))
        self.assertEqual(loss, expected_loss)


class TestTextEmbeddingDualEncoder(TestCase):
    """Tests TextEmbeddingDualEncoder."""

    def sample_text_embedding_dual_encoder_confg(self) -> TextEmbeddingDualEncoder.Config:
        doc_stream_encoder_config = {
            LEFT_ENCODER_NAME: sample_text_embedding_stream_encoder_config(output_dim=HIDDEN_DIM),
            RIGHT_ENCODER_NAME: sample_text_embedding_stream_encoder_config(
                output_dim=HIDDEN_DIM,
            ),
        }

        model_cfg = TextEmbeddingDualEncoder.default_config().set(
            stream_encoder=doc_stream_encoder_config,
            fusion_network={
                "contrastive": sample_contrastive_loss_layer_config(),
            },
        )
        return model_cfg

    def test_simple(self):
        model_cfg = self.sample_text_embedding_dual_encoder_confg()
        model_cfg.set(name="test_simple")
        model = model_cfg.instantiate(parent=None)
        model_params = model.initialize_parameters_recursively(jax.random.PRNGKey(0))

        input_batch = {
            LEFT_ENCODER_NAME: {
                POSITIVE_INPUT_IDS: random_int_array(shape=(2, 1, 4)),
            },
            RIGHT_ENCODER_NAME: {
                POSITIVE_INPUT_IDS: random_int_array(shape=(2, 1, 4)),
                POSITIVE_PADDINGS: jnp.zeros((2, 1)),
                NEGATIVE_INPUT_IDS: random_int_array(shape=(2, 3, 4)),
                NEGATIVE_PADDINGS: jnp.asarray([[0, 0, 0], [0, 1, 1]]),
            },
        }

        outputs, _ = F(
            model,
            inputs=dict(input_batch=input_batch),
            state=model_params,
            is_training=True,
            prng_key=jax.random.PRNGKey(0),
        )
        loss = outputs[0]
        assert loss.shape == ()


class TestSiameseTextEmbeddingDualEncoder(TestCase):
    """Tests SiameseTextEmbeddingDualEncoder."""

    def sample_shared_text_embedding_dual_encoder_confg(
        self,
    ) -> TextEmbeddingDualEncoder.Config:
        shared_encoder_cfg = sample_text_embedding_stream_encoder_config(output_dim=HIDDEN_DIM)
        passage_encoder_cfg = RedirectToSharedModule.default_config().set(
            shared_module=TEXT_DUAL_ENCODER_SHARED_MODULE_NAME
        )

        doc_stream_encoder_config = {
            LEFT_ENCODER_NAME: shared_encoder_cfg,
            RIGHT_ENCODER_NAME: passage_encoder_cfg,
        }

        model_cfg = TextEmbeddingDualEncoder.default_config().set(
            shared_encoder_name=LEFT_ENCODER_NAME,
            stream_encoder=doc_stream_encoder_config,
            fusion_network={
                "contrastive": sample_contrastive_loss_layer_config(),
            },
        )
        return model_cfg

    def test_simple(self):
        model_cfg = self.sample_shared_text_embedding_dual_encoder_confg()
        model_cfg.set(name="test_simple_shared_dual_encoder")
        model = model_cfg.instantiate(parent=None)
        model_params = model.initialize_parameters_recursively(jax.random.PRNGKey(0))

        # Assertion to test if there's only 1 copy of weights.
        assert model_params[LEFT_ENCODER_NAME] != {}
        assert model_params[RIGHT_ENCODER_NAME] == {}

        input_batch = {
            LEFT_ENCODER_NAME: {
                POSITIVE_INPUT_IDS: random_int_array(shape=(2, 1, 4)),
            },
            RIGHT_ENCODER_NAME: {
                POSITIVE_INPUT_IDS: random_int_array(shape=(2, 1, 4)),
                POSITIVE_PADDINGS: jnp.zeros((2, 1)),
                NEGATIVE_INPUT_IDS: random_int_array(shape=(2, 3, 4)),
                NEGATIVE_PADDINGS: jnp.asarray([[0, 0, 0], [0, 1, 1]]),
            },
        }

        outputs, _ = F(
            model,
            inputs=dict(input_batch=input_batch),
            state=model_params,
            is_training=True,
            prng_key=jax.random.PRNGKey(0),
        )
        loss = outputs[0]
        assert loss.shape == ()
