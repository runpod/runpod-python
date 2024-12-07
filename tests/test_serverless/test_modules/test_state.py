""" Tests for environment variables module """

import os
import unittest

from runpod.serverless.modules.worker_state import (
    Job,
    JobsProgress,
    IS_LOCAL_TEST,
    WORKER_ID,
)


class TestEnvVars(unittest.TestCase):
    """Tests for environment variables module"""

    def setUp(self):
        """
        Set up test variables
        """
        self.test_api_key = "test_api_key"
        os.environ["RUNPOD_AI_API_KEY"] = self.test_api_key

    def test_is_local_test(self):
        """
        Tests if IS_LOCAL_TEST flag is properly set
        """
        os.environ.pop("RUNPOD_WEBHOOK_GET_JOB", None)
        self.assertEqual(IS_LOCAL_TEST, True)

    def test_worker_id(self):
        """
        Tests if WORKER_ID is properly set
        """
        os.environ["RUNPOD_POD_ID"] = WORKER_ID

        self.assertEqual(WORKER_ID, os.environ.get("RUNPOD_POD_ID"))


class TestJob(unittest.TestCase):

    def test_initialization_with_basic_attributes(self):
        """Test basic initialization of Job object."""
        job = Job(id="job_123", input={"task": "data_process"}, webhook="http://example.com/webhook")
        self.assertEqual(job.id, "job_123")
        self.assertEqual(job.input, {"task": "data_process"})
        self.assertEqual(job.webhook, "http://example.com/webhook")

    def test_initialization_with_additional_kwargs(self):
        """Test initialization with extra kwargs dynamically creating attributes."""
        job = Job(id="job_456", status="pending", priority=5)
        self.assertEqual(job.id, "job_456")
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.priority, 5)

    def test_equality(self):
        """Test equality between two Job objects based on the job ID."""
        job1 = Job(id="job_123")
        job2 = Job(id="job_123")
        job3 = Job(id="job_456")
        
        self.assertEqual(job1, job2)
        self.assertNotEqual(job1, job3)

    def test_hashing(self):
        """Test hashing of Job object based on the job ID."""
        job1 = Job(id="job_123")
        job2 = Job(id="job_123")
        job3 = Job(id="job_456")
        
        self.assertEqual(hash(job1), hash(job2))
        self.assertNotEqual(hash(job1), hash(job3))

    def test_string_representation(self):
        """Test the string representation of the Job object."""
        job = Job(id="job_123")
        self.assertEqual(str(job), "job_123")

    def test_none_input(self):
        """Test initialization with None values."""
        job = Job(id="job_123", input=None, webhook=None)
        self.assertEqual(job.id, "job_123")
        self.assertIsNone(job.input)
        self.assertIsNone(job.webhook)

    def test_dynamic_kwargs_assignment(self):
        """Test if kwargs are dynamically assigned as attributes."""
        job = Job(id="job_789", foo="bar", custom_attr=42)
        self.assertEqual(job.foo, "bar")
        self.assertEqual(job.custom_attr, 42)

    def test_missing_attributes(self):
        """Test that accessing non-existent attributes raises AttributeError."""
        job = Job(id="job_123")
        with self.assertRaises(AttributeError):
            _ = job.non_existent_attr


class TestJobsProgress(unittest.IsolatedAsyncioTestCase):
    """Tests for JobsProgress class"""

    async def asyncSetUp(self):
        """
        Set up test variables
        """
        self.jobs = JobsProgress()
        self.jobs.clear()  # clear jobs before each test

    def test_singleton(self):
        jobs2 = JobsProgress()
        self.assertEqual(self.jobs, jobs2)

    async def test_add_job(self):
        assert not self.jobs.get_job_count()

        id = "123"
        self.jobs.add({"id": id})
        assert self.jobs.get_job_count() == 1

        job1 = self.jobs.get(id)
        assert job1 in self.jobs

        id = "234"
        self.jobs.add(id)
        assert self.jobs.get_job_count() == 2

        job2 = self.jobs.get(id)
        assert job2 in self.jobs

    async def test_remove_job(self):
        assert not self.jobs.get_job_count()

        job = {"id": "123"}
        self.jobs.add(job)
        assert self.jobs.get_job_count()

        self.jobs.remove("123")
        assert not self.jobs.get_job_count()

    async def test_get_job(self):
        for id in ["123", "234", "345"]:
            self.jobs.add({"id": id})

        job1 = self.jobs.get(id)
        assert job1 in self.jobs

    async def test_get_job_list(self):
        assert self.jobs.get_job_list() is None

        job1 = {"id": "123"}
        self.jobs.add(job1)

        job2 = {"id": "456"}
        self.jobs.add(job2)

        assert self.jobs.get_job_count() == 2
        assert self.jobs.get_job_list() in ["123,456", "456,123"]

    async def test_get_job_count(self):
        # test job count contention when adding and removing jobs in parallel
        pass