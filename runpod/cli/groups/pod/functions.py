'''
RunPod | CLI | Pod | Functions
'''
import configparser

from runpod import create_pod

def pod_from_template(template_file):
    '''
    Creates a pod from a template file.
    '''
    pod_config = configparser.ConfigParser()
    pod_config.read(template_file)
    new_pod = create_pod(
        pod_config['pod'].pop('name'), pod_config['pod'].pop('image'),
        pod_config['pod'].pop('gpu_type'), **pod_config['pod'])

    return new_pod
