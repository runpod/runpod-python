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
    def __init__(self):
        """
        Initialize an instance of MetricsCollector.

        The constructor initializes an empty metrics dictionary to store collected metrics.
        """
        self.metrics = {}

    def push_metrics_internal(self, job_id, metrics):
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


    def pop_metrics_internal(self, job_id):
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
        if self.metrics[job_id]:
            metrics = self.metrics[job_id]
            del self.metrics[job_id]
            return metrics
        return None
