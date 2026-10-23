"""Domain exceptions; never caught broadly, always translated at the CLI edge."""

from __future__ import annotations


class CloudPredictError(Exception):
    """Base class for every domain-level failure."""


class DataFormatError(CloudPredictError):
    """A CSV or log file does not match the canonical schema."""


class UnsupportedSourceError(CloudPredictError):
    """The requested ingest source has no available adapter."""


class EstimatorNotTrainedError(CloudPredictError):
    """A learned estimator was requested before ``train`` produced a model."""
