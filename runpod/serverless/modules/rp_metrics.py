"""
This class offers internal utilities designed to manage RunPod metrics.
"""

class MetricsCollector():
    """
    A class for collecting and storing metrics for various jobs.

    This class provides a mechanism for collecting and organizing metrics associated with
    different jobs. It allows you to store metrics internally and retrieve them as needed.

    Attributes:
        metrics (dict): A dictionary to store collected metrics. The keys are job IDs, and
            the values are dictionaries containing metric names and corresponding measurements.

    Methods:
        push_metrics_internal(job_id, metrics):
            Store metrics for a specific job internally.

    Example:
        >>> metrics_collector = MetricsCollector()
        >>> job_id = "job123"
        >>> metrics = {"accuracy": 0.85, "loss": 0.15}
        >>> metrics_collector.push_metrics_internal(job_id, metrics)
    """
    _instance = None
    metrics = {}
    job_stream_agg_func = {}

    def __new__(cls):
        """
        Initialize an instance of MetricsCollector.

        The constructor initializes an empty metrics dictionary to store collected metrics.
        """
        if MetricsCollector._instance is None:
            MetricsCollector._instance = object.__new__(cls)
            MetricsCollector._instance.metrics = {}
        return MetricsCollector._instance

    def push_metrics_internal(self, job_id: str, metrics):
        """
        Store metrics for a specific job internally.

        This method allows you to store and associate a set of metrics with a specific job ID
        within the internal metrics storage of the object.

        Args:
            job_id (str): The unique identifier for the job.
            metrics (dict): A dictionary containing the metrics associated with the job.
                The dictionary should have metric names as keys and corresponding values
                as metric measurements.

        Returns:
            None

        Example:
            >>> metrics_collector = MetricsCollector()
            >>> job_id = "job123"
            >>> metrics = {"accuracy": 0.85, "loss": 0.15}
            >>> metrics_collector.push_metrics_internal(job_id, metrics)
        """
        self.metrics[job_id] = metrics


    def pop_metrics_internal(self, job_id: str):
        """
        Remove metrics associated with a specific job ID from internal storage.

        This method allows you to remove the metrics associated with a specific job ID from
        the internal metrics storage of the object.

        Args:
            job_id (str): The unique identifier for the job whose metrics need to be removed.

        Returns:
            None

        Example:
            >>> metrics_collector = MetricsCollector()
            >>> job_id = "job123"
            >>> metrics_collector.pop_metrics_internal(job_id)
        """
        if job_id in self.metrics:
            metrics = self.metrics[job_id]
            del self.metrics[job_id]
            return metrics
        return None


    def update_stream_aggregate(self, job_id: str, aggregate_function):
        """
        Updates the aggregate function for a job's stream output.
        """
        self.job_stream_agg_func[job_id] = aggregate_function


    def pop_stream_aggregate(self, job_id: str):
        """
        Pop the aggregate function for an aggregate job stream.
        """
        if job_id in self.job_stream_agg_func:
            job_stream_agg_func = self.job_stream_agg_func[job_id]
            del self.job_stream_agg_func[job_id]
            return job_stream_agg_func
        return None

metrics_collector = MetricsCollector()
