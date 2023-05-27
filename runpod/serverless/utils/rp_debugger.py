'''
runpod | serverless | rp_debugger.py

A collection of functions to help with debugging.
'''

import time


class Checkpoints:
    '''
    A singleton class to store checkpoint times.

    Format:
    [
        {
            'name': 'checkpoint_name',
            'start': 1234567890.123456,
            'end': 1234567890.123456
            'duration_ms': 1234567890.123456
        },
    ]

    Usage:
    from rp_debugger import Checkpoints

    checkpoints = Checkpoints()

    # Add a checkpoint
    checkpoints.add('checkpoint_name')

    # Start a checkpoint
    checkpoints.start('checkpoint_name')

    # Stop a checkpoint
    checkpoints.stop('checkpoint_name')
    '''
    __instance = None

    def __new__(cls):
        if Checkpoints.__instance is None:
            Checkpoints.__instance = object.__new__(cls)
        return Checkpoints.__instance

    def __init__(self, checkpoint_list=None):
        self.checkpoints = []
        self.name_lookup = {}

        self.persistent_checkpoints = checkpoint_list if checkpoint_list else []

        if checkpoint_list is not None:

            for checkpoint in checkpoint_list:
                self.add(checkpoint['name'])

    def add(self, name):
        '''
        Add a checkpoint.
        Returns the index of the checkpoint.
        '''
        if name in self.name_lookup:
            raise KeyError('Checkpoint name already exists.')

        self.checkpoints.append({
            'name': name
        })

        index = len(self.checkpoints) - 1
        self.name_lookup[name] = index

    def start(self, name):
        '''
        Start a checkpoint.
        '''
        if name not in self.name_lookup:
            raise KeyError('Checkpoint name does not exist.')

        index = self.name_lookup[name]
        self.checkpoints[index]['start'] = time.perf_counter()

    def stop(self, name):
        '''
        Stop a checkpoint.
        '''
        if name not in self.name_lookup:
            raise KeyError('Checkpoint name does not exist.')

        index = self.name_lookup[name]

        if 'start' not in self.checkpoints[index]:
            raise KeyError('Checkpoint has not been started.')

        self.checkpoints[index]['end'] = time.perf_counter()

    def get_checkpoints(self):
        '''
        Get the results of the checkpoints.
        '''
        results = []
        for checkpoint in self.checkpoints:
            if 'start' not in checkpoint or 'end' not in checkpoint:
                continue

            checkpoint['duration_ms'] = checkpoint['end'] - checkpoint['start']
            results.append(checkpoint)

        return results

    def clear(self):
        '''
        Clear the checkpoints.
        '''
        self.checkpoints = []
        self.name_lookup = {}

        for name in self.persistent_checkpoints:
            self.add(name)


def benchmark(function):
    '''
    A decorator to benchmark a function.
    '''
    def wrapper(*args, **kwargs):
        checkpoints = Checkpoints()
        checkpoints.add(function.__name__)
        checkpoints.start(function.__name__)

        result = function(*args, **kwargs)

        checkpoints.stop(function.__name__)

        return result

    return wrapper


def get_debugger_output():
    '''
    Get the debugger output.
    '''
    checkpoints = Checkpoints()
    ckpt_results = checkpoints.get_checkpoints()
    checkpoints.clear()

    return ckpt_results
