'''
RunPod | CLI | Project | Commands
'''

import os
import sys
import click
from InquirerPy import prompt as cli_select

from runpod import get_user
from .functions import create_new_project, start_project, create_project_endpoint
from .helpers import validate_project_name


@click.group('project', help='Create, deploy, and manage RunPod projects.')
def project_cli():
    ''' Create and deploy projects on RunPod. '''


# -------------------------------- New Project ------------------------------- #
@project_cli.command('new')
@click.option('--name', '-n', 'project_name', type=str, default=None, help="The project name.")
@click.option('--type', '-t', 'model_type', type=click.Choice(['llama2'], case_sensitive=False),
              default=None, help="The type of Hugging Face model.")
@click.option('--model', '-m', 'model_name', type=str, default=None,
              help="The name of the Hugging Face model. (e.g. meta-llama/Llama-2-7b)")
@click.option('--init', '-i', 'init_current_dir', is_flag=True, default=False)
def new_project_wizard(project_name, model_type, model_name, init_current_dir):
    """ Create a new project. """
    network_volumes = get_user()['networkVolumes']
    if len(network_volumes) == 0:
        click.echo("You do not have any network volumes.")
        click.echo(
            "Please create a network volume (https://runpod.io/console/user/storage) and try again.")  # pylint: disable=line-too-long
        sys.exit(1)

    click.echo("Creating a new project...")

    if init_current_dir:
        project_name = os.path.basename(os.getcwd())

    if project_name is None:
        project_name = click.prompt("   > Enter the project name", type=str)

    validate_project_name(project_name)

    def print_net_vol(vol):
        return {
            'name': f"{vol['id']}: {vol['name']} ({vol['size']} GB, {vol['dataCenterId']})",
            'value': vol['id']
        }

    network_volumes = list(map(print_net_vol, network_volumes))
    questions = [
        {
            'type': 'rawlist',
            'name': 'volume-id',
            'qmark': '',
            'amark': '',
            'message': '   > Select a Network Volume:',
            'choices': network_volumes
        }
    ]
    runpod_volume_id = cli_select(questions)['volume-id']

    cuda_version = click.prompt(
        "   > Select a CUDA version, or press enter to use the default",
        type=click.Choice(['11.1.1', '11.8.0', '12.1.0'], case_sensitive=False),
        default='11.8.0'
    )

    python_version = click.prompt(
        "   > Select a Python version, or press enter to use the default",
        type=click.Choice(['3.8', '3.9', '3.10', '3.11'], case_sensitive=False),
        default='3.10'
    )

    click.echo("")
    click.echo("Project Summary:")

    click.echo(f"   - Project Name: {project_name}")
    click.echo(f"   - RunPod Network Storage ID: {runpod_volume_id}")
    click.echo(f"   - CUDA Version: {cuda_version}")
    click.echo(f"   - Python Version: {python_version}")

    click.echo("")
    click.echo("The project will be created in the current directory.")
    click.confirm("Do you want to continue?", abort=True)

    create_new_project(project_name, runpod_volume_id, cuda_version,
                       python_version, model_type, model_name, init_current_dir)  # pylint: disable=too-many-arguments

    click.echo(f"Project {project_name} created successfully!")
    click.echo("")
    click.echo("From your project root run `runpod project start` to start a development pod.")


# ------------------------------- Start Project ------------------------------ #
@project_cli.command('start')
def start_project_pod():
    '''
    Start the project development pod from runpod.toml
    '''
    click.echo("Starting the project will create a development pod on RunPod, if one does not already exist.")  # pylint: disable=line-too-long
    click.echo("    - You will be charged based on the GPU type specified in runpod.toml.")
    click.echo("    - When you are finished close the connection with CTRL+C.")
    click.echo("")
    click.confirm("Do you want to continue?", abort=True)

    click.echo("Starting project development pod...")

    start_project()


# ------------------------------ Deploy Project ------------------------------ #
@project_cli.command('deploy')
def deploy_project():
    """ Deploy the project to RunPod. """
    click.echo("Deploying project...")

    endpoint_id = create_project_endpoint()

    click.echo(f"Project deployed successfully! Endpoint ID: {endpoint_id}")
    click.echo("The following urls are available:")
    click.echo(f"    - https://api.runpod.ai/v2/{endpoint_id}/runsync")
    click.echo(f"    - https://api.runpod.ai/v2/{endpoint_id}/run")
    click.echo(f"    - https://api.runpod.ai/v2/{endpoint_id}/health")
