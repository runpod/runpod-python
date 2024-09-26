""" Tests for environment variables module """

import os
import unittest

from runpod.serverless.modules.worker_state import (
    Job,
    JobsQueue,
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


class TestJobsQueue(unittest.IsolatedAsyncioTestCase):
    """Tests for Jobs class"""

    def setUp(self):
        """
        Set up test variables
        """
        self.jobs = JobsQueue()

    async def asyncTearDown(self):
        await self.jobs.clear()  # clear jobs before each test

    def test_singleton(self):
        """
        Tests if Jobs is a singleton class
        """
        jobs2 = JobsQueue()
        self.assertEqual(self.jobs, jobs2)

    async def test_add_job(self):
        """
        Tests if add_job() method works as expected
        """
        assert not self.jobs.get_job_count()

        job_input = {"id": "123"}
        await self.jobs.add_job(job_input)

        assert self.jobs.get_job_count() == 1

    async def test_remove_job(self):
        """
        Tests if get_job() method removes the job from the queue
        """
        job = {"id": "123"}
        await self.jobs.add_job(job)
        await self.jobs.get_job()
        assert job not in self.jobs

    async def test_get_job(self):
        """
        Tests if get_job() is FIFO
        """
        job1 = {"id": "123"}
        await self.jobs.add_job(job1)

        job2 = {"id": "456"}
        await self.jobs.add_job(job2)

        next_job = await self.jobs.get_job()
        assert next_job not in self.jobs
        assert next_job == job1

        next_job = await self.jobs.get_job()
        assert next_job not in self.jobs
        assert next_job == job2

    async def test_get_job_list(self):
        """
        Tests if get_job_list() returns comma-separated IDs
        """
        self.assertTrue(self.jobs.get_job_list() is None)

        job1 = {"id": "123"}
        await self.jobs.add_job(job1)

        job2 = {"id": "456"}
        await self.jobs.add_job(job2)

        assert self.jobs.get_job_count() == 2
        assert job1 in self.jobs
        assert job2 in self.jobs
        assert self.jobs.get_job_list() in ["123,456", "456,123"]
