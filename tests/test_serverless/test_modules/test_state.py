''' Tests for environment variables module '''

import os
import unittest

from runpod.serverless.modules.worker_state import (
    Job, Jobs, IS_LOCAL_TEST, WORKER_ID
)


class TestEnvVars(unittest.TestCase):
    ''' Tests for environment variables module '''

    def setUp(self):
        '''
        Set up test variables
        '''
        self.test_api_key = 'test_api_key'
        os.environ['RUNPOD_AI_API_KEY'] = self.test_api_key

    def test_is_local_test(self):
        '''
        Tests if IS_LOCAL_TEST flag is properly set
        '''
        os.environ.pop('RUNPOD_WEBHOOK_GET_JOB', None)
        self.assertEqual(IS_LOCAL_TEST, True)

    def test_worker_id(self):
        '''
        Tests if WORKER_ID is properly set
        '''
        os.environ["RUNPOD_POD_ID"] = WORKER_ID

        self.assertEqual(WORKER_ID, os.environ.get('RUNPOD_POD_ID'))


class TestJobs(unittest.TestCase):
    ''' Tests for Jobs class '''

    def setUp(self):
        '''
        Set up test variables
        '''
        self.jobs = Jobs()
        self.jobs.jobs.clear()  # clear jobs before each test

    def test_singleton(self):
        '''
        Tests if Jobs is a singleton class
        '''
        jobs2 = Jobs()
        self.assertEqual(self.jobs, jobs2)

    def test_add_job(self):
        '''
        Tests if add_job() method works as expected
        '''
        self.jobs.add_job('123')
        self.assertIn(Job('123'), self.jobs.jobs)

    def test_remove_job(self):
        '''
        Tests if remove_job() method works as expected
        '''
        self.jobs.add_job('123')
        self.jobs.remove_job('123')
        self.assertNotIn(Job('123'), self.jobs.jobs)

    def test_get_job_input(self):
        '''
        Tests if get_job_input() method works as expected
        '''
        job1 = Job(job_id="id1")
        job2 = Job(job_id="id2")
        self.assertNotEqual(job1, job2)

        job = Job(job_id="id1")
        non_job_object = "some_string"
        self.assertNotEqual(job, non_job_object)

        self.assertEqual(self.jobs.get_job('123'), None)

        self.jobs.add_job('123', 'test_input')
        self.assertEqual(self.jobs.get_job('123').input, 'test_input')

    def test_get_job_list(self):
        '''
        Tests if get_job_list() method works as expected
        '''
        self.assertTrue(self.jobs.get_job_list() is None)

        self.jobs.add_job('123')
        self.jobs.add_job('456')
        self.assertEqual(len(self.jobs.jobs), 2)
        self.assertTrue(Job('123') in self.jobs.jobs)
        self.assertTrue(Job('456') in self.jobs.jobs)

        self.assertTrue(self.jobs.get_job_list() in ['123,456', '456,123'])
