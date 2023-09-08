'''
RunPod | CLI | Project | Commands
'''

import re
import click

from .functions import create_new_project, launch_project, start_project_api

@click.group('project')
def project_cli():
    ''' Launch new project on RunPod. '''

def validate_project_name(name):
    '''
    Validate the project name.
    '''
    match = re.search(r"[<>:\"/\\|?*\s]", name)
    if match:
        raise click.BadParameter(f"Project name contains an invalid character: '{match.group()}'.")
    return name

# -------------------------------- New Project ------------------------------- #
@project_cli.command('new')
@click.option('--name', '-n', 'project_name', type=str, default=None, help="The project name.")
@click.option('--type', '-t', 'model_type', type=click.Choice(['llama2'], case_sensitive=False),
              default=None, help="The type of Hugging Face model.")
@click.option('--model', '-m', 'model_name', type=str, default=None,
              help="The name of the Hugging Face model. (e.g. meta-llama/Llama-2-7b)")
def new_project_wizard(project_name, model_type, model_name):
    '''
    Create a new project.
    '''
    if project_name is None:
        project_name = click.prompt("Enter the project name", type=str)
    validate_project_name(project_name)

    click.echo("Projects require RunPod network storage. https://runpod.io/console/user/storage")
    runpod_volume_id = click.prompt("Enter the ID of the volume to use", type=str)

    python_version = click.prompt(
        "Select a Python version",
        type=click.Choice(['3.10', '3.11'], case_sensitive=False),
        default='3.10'
    )

    click.echo(f"Project Name: {project_name}")
    click.echo(f"RunPod Volume ID: {runpod_volume_id}")
    click.echo(f"Python Version: {python_version}")

    click.echo("The project will be created in the current directory.")
    click.confirm("Do you want to continue?", abort=True)

    create_new_project(project_name, runpod_volume_id, python_version, model_type, model_name)

    click.echo(f"Project {project_name} created successfully!")
    click.echo(f"Navigate to the project folder with `cd {project_name}`.")
    click.echo("Run `runpod project launch` to launch the project development environment.")


# ------------------------------ Launch Project ------------------------------ #
@project_cli.command('launch')
def launch_project_pod():
    '''
    Launch the project development environment from runpod.toml
    '''
    click.echo("Launching the project will create a new pod on RunPod.")
    click.echo("You will be charged based on the GPU type specified in runpod.toml.")
    click.echo("When you are finished with the pod you will need to delete it manually.")
    click.confirm("Do you want to continue?", abort=True)

    click.echo("Launching project development environment...")
    launch_project()


# ------------------------------- Start Project ------------------------------ #
@project_cli.command('start')
def start_project_pod():
    '''
    Starts the API server from the handler file.
    '''
    click.echo("Starting project API server...")
    start_project_api()
