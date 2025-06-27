"""
Runpod | CLI | Project | Functions
"""

import os
import sys
import uuid
from datetime import datetime

import tomlkit
from tomlkit import comment, document, nl, table

from runpod import (
    __version__,
    create_endpoint,
    create_template,
    get_pod,
    update_endpoint_template,
)
from runpod.cli import BASE_DOCKER_IMAGE, ENV_VARS, GPU_TYPES
from runpod.cli.utils.ssh_cmd import SSHConnection

from ...utils.rp_sync import sync_directory
from .helpers import (
    attempt_pod_launch,
    copy_template_files,
    get_project_endpoint,
    get_project_pod,
    load_project_config,
)

STARTER_TEMPLATES = os.path.join(os.path.dirname(__file__), "starter_templates")


def _launch_dev_pod():
    """Launch a development pod."""
    config = load_project_config()  # Load runpod.toml

    print("Deploying development pod on Runpod...")

    # Prepare the environment variables
    environment_variables = {"RUNPOD_PROJECT_ID": config["project"]["uuid"]}
    for variable in config["project"].get("env_vars", {}):
        environment_variables[variable] = config["project"]["env_vars"][variable]

    # Prepare the GPU types
    selected_gpu_types = config["project"].get("gpu_types", [])
    if config["project"].get("gpu", None):
        selected_gpu_types.append(config["project"]["gpu"])

    # Attempt to launch a pod with the given configuration
    new_pod = attempt_pod_launch(config, environment_variables)
    if new_pod is None:
        print(
            "Selected GPU types unavailable, try again later or use a different type."
        )
        return None

    print("Waiting for pod to come online... ", end="")
    sys.stdout.flush()

    # Wait for the pod to come online
    while (
        new_pod.get("desiredStatus", None) != "RUNNING"
        or new_pod.get("runtime") is None
    ):
        new_pod = get_pod(new_pod["id"])

    project_pod_id = new_pod["id"]

    print(
        f"Project {config['project']['name']} pod ({project_pod_id}) created.",
        end="\n\n",
    )
    return project_pod_id


# -------------------------------- New Project ------------------------------- #
def create_new_project(
    project_name,
    runpod_volume_id,
    cuda_version,
    python_version,  # pylint: disable=too-many-locals, too-many-arguments, too-many-statements
    model_type=None,
    model_name=None,
    init_current_dir=False,
):
    """Create a new project."""
    if not init_current_dir:
        project_folder = os.path.join(os.getcwd(), project_name)
        if not os.path.exists(project_folder):
            os.makedirs(project_folder)

        if model_type is None:
            model_type = "default"

        template_dir = os.path.join(STARTER_TEMPLATES, model_type)

        copy_template_files(template_dir, project_folder)

        # Replace placeholders in requirements.txt
        requirements_path = os.path.join(project_folder, "builder/requirements.txt")
        with open(requirements_path, "r", encoding="utf-8") as requirements_file:
            requirements_content = requirements_file.read()

        if "dev" in __version__:
            requirements_content = requirements_content.replace(
                "<<RUNPOD>>", "git+https://github.com/runpod/runpod-python.git"
            )
        else:
            requirements_content = requirements_content.replace(
                "<<RUNPOD>>", f"runpod=={__version__}"
            )

        with open(requirements_path, "w", encoding="utf-8") as requirements_file:
            requirements_file.write(requirements_content)

        # If there's a model_name, replace placeholders in handler.py
        if model_name:
            handler_path = os.path.join(project_name, "src/handler.py")
            with open(handler_path, "r", encoding="utf-8") as file:
                handler_content = file.read()
                handler_content = handler_content.replace("<<MODEL_NAME>>", model_name)

            with open(handler_path, "w", encoding="utf-8") as file:
                file.write(handler_content)
    else:
        project_folder = os.getcwd()

    project_uuid = str(uuid.uuid4())[:8]

    toml_config = document()
    toml_config.add(comment("Runpod Project Configuration"))
    toml_config.add(nl())
    toml_config.add("title", project_name)

    project_table = table()
    project_table.add("uuid", project_uuid)
    project_table.add("name", project_name)
    project_table.add("base_image", BASE_DOCKER_IMAGE.format(cuda_version=cuda_version))
    project_table.add("gpu_types", GPU_TYPES)
    project_table.add("gpu_count", 1)
    project_table.add("storage_id", runpod_volume_id)
    project_table.add("volume_mount_path", "/runpod-volume")
    project_table.add("ports", "8080/http, 22/tcp")
    project_table.add("container_disk_size_gb", 10)
    project_table.add("env_vars", ENV_VARS)
    toml_config.add("project", project_table)

    template_table = table()
    template_table.add("model_type", str(model_type))
    template_table.add("model_name", str(model_name))
    toml_config.add("template", template_table)

    runtime_table = table()
    runtime_table.add("python_version", python_version)
    runtime_table.add("handler_path", "src/handler.py")
    runtime_table.add("requirements_path", "builder/requirements.txt")
    toml_config.add("runtime", runtime_table)

    with open(
        os.path.join(project_folder, "runpod.toml"), "w", encoding="UTF-8"
    ) as config_file:
        tomlkit.dump(toml_config, config_file)


# ------------------------------- Start Project ------------------------------ #
def start_project():  # pylint: disable=too-many-locals, too-many-branches
    """
    Start the project development environment from runpod.toml
    - Check if the project pod already exists.

    - If the project pod does not exist:
        # SSH into the pod and create a project folder within the volume
        # Create a folder in the volume for the project that matches the project uuid
        # Create a folder in the project folder that matches the project name
        # Copy the project files into the project folder
        # crate a virtual environment using the python version specified in the project config
        # install the requirements.txt file

    - If the project pod does exist:

    """
    config = load_project_config()  # Load runpod.toml

    for config_item in config["project"]:
        print(f'    - {config_item}: {config["project"][config_item]}')
    print("")

    project_pod_id = get_project_pod(config["project"]["uuid"])

    # Check if the project pod already exists, if not create it.
    if not project_pod_id:
        project_pod_id = _launch_dev_pod()

    if project_pod_id is None:
        return

    with SSHConnection(project_pod_id) as ssh_conn:

        project_path_uuid = (
            f'{config["project"]["volume_mount_path"]}/{config["project"]["uuid"]}'
        )
        project_path_uuid_dev = os.path.join(project_path_uuid, "dev")
        project_path_uuid_prod = os.path.join(project_path_uuid, "prod")
        remote_project_path = os.path.join(
            project_path_uuid_dev, config["project"]["name"]
        )

        # Create the project folder on the pod
        print(
            f"Checking pod project folder: {remote_project_path} on pod {project_pod_id}"
        )
        ssh_conn.run_commands(
            [f"mkdir -p {remote_project_path} {project_path_uuid_prod}"]
        )

        # Copy local files to the pod project folder
        print(f"Syncing files to pod {project_pod_id}")
        ssh_conn.rsync(os.getcwd(), project_path_uuid_dev)

        # Create the virtual environment
        venv_path = os.path.join(project_path_uuid_dev, "venv")
        print(
            f"Activating Python virtual environment: {venv_path} on pod {project_pod_id}"
        )
        commands = [
            f'python{config["runtime"]["python_version"]} -m virtualenv {venv_path}',
            f"source {venv_path}/bin/activate &&"
            f"cd {remote_project_path} &&"
            "python -m pip install --upgrade pip &&"
            f'python -m pip install -v --requirement {config["runtime"]["requirements_path"]}',
        ]
        ssh_conn.run_commands(commands)

        # Start the watcher and then start the API development server
        sync_directory(ssh_conn, os.getcwd(), project_path_uuid_dev)

        project_name = config["project"]["name"]
        pip_req_path = os.path.join(
            remote_project_path, config["runtime"]["requirements_path"]
        )
        handler_path = os.path.join(
            remote_project_path, config["runtime"]["handler_path"]
        )

        launch_api_server = [
            f"""
            pkill inotify

            function force_kill {{
                kill $1 2>/dev/null
                sleep 1

                if ps -p $1 > /dev/null; then
                    echo "Graceful kill failed, attempting SIGKILL..."
                    kill -9 $1 2>/dev/null
                    sleep 1

                    if ps -p $1 > /dev/null; then
                        echo "Failed to kill process with PID: $1"
                        exit 1
                    else
                        echo "Killed process with PID: $1 using SIGKILL"
                    fi

                else
                    echo "Killed process with PID: $1"
                fi
            }}

            function cleanup {{
                echo "Cleaning up..."
                force_kill $last_pid
            }}

            trap cleanup EXIT SIGINT

            if source {project_path_uuid_dev}/venv/bin/activate; then
                echo -e "- Activated virtual environment."
            else
                echo "Failed to activate virtual environment."
                exit 1
            fi

            if cd {project_path_uuid_dev}/{project_name}; then
                echo -e "- Changed to project directory."
            else
                echo "Failed to change directory."
                exit 1
            fi

            exclude_pattern='(__pycache__|\\.pyc$)'
            function update_exclude_pattern {{
                exclude_pattern='(__pycache__|\\.pyc$)'
                if [[ -f .runpodignore ]]; then
                    while IFS= read -r line; do
                        line=$(echo "$line" | tr -d '[:space:]')
                        [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue # Skip comments and empty lines
                        exclude_pattern="${{exclude_pattern}}|(${{line}})"
                    done < .runpodignore
                    echo -e "- Ignoring files matching pattern: $exclude_pattern"
                fi
            }}
            update_exclude_pattern

            # Start the API server in the background, and save the PID
            python {handler_path} --rp_serve_api --rp_api_host="0.0.0.0" --rp_api_port=8080 --rp_api_concurrency=1 &
            last_pid=$!

            echo -e "- Started API server with PID: $last_pid" && echo ""
            echo "Connect to the API server at:"
            echo ">  https://$RUNPOD_POD_ID-8080.proxy.runpod.net" && echo ""

            while true; do
                if changed_file=$(inotifywait -q -r -e modify,create,delete --exclude "$exclude_pattern" {remote_project_path} --format '%w%f'); then
                    echo "Detected changes in: $changed_file"
                else
                    echo "Failed to detect changes."
                    exit 1
                fi

                force_kill $last_pid

                if [[ $changed_file == *"requirements"* ]]; then
                    echo "Installing new requirements..."
                    python -m pip install --upgrade pip && python -m pip install -r {pip_req_path}
                fi

                if [[ $changed_file == *".runpodignore"* ]]; then
                    update_exclude_pattern
                fi

                python {handler_path} --rp_serve_api --rp_api_host="0.0.0.0" --rp_api_port=8080 --rp_api_concurrency=1 &
                last_pid=$!

                echo "Restarted API server with PID: $last_pid"
            done
        """
        ]

        print("")
        print("Starting project development endpoint...")
        ssh_conn.run_commands(launch_api_server)


# ------------------------------ Deploy Project ------------------------------ #
def create_project_endpoint():
    """Create a project endpoint.
    - Move code in dev to prod folder
    - TODO: git commit the diff from current state to new state
    - Create a serverless template for the project
    - Create a new endpoint using the template
    """
    config = load_project_config()
    project_pod_id = get_project_pod(config["project"]["uuid"])

    # Check if the project pod already exists, if not create it.
    if not project_pod_id:
        project_pod_id = _launch_dev_pod()

    if project_pod_id is None:
        return None

    with SSHConnection(project_pod_id) as ssh_conn:
        project_path_uuid = (
            f'{config["project"]["volume_mount_path"]}/{config["project"]["uuid"]}'
        )
        project_path_uuid_prod = os.path.join(project_path_uuid, "prod")
        remote_project_path = os.path.join(
            project_path_uuid_prod, config["project"]["name"]
        )

        # Copy local files to the pod project folder
        ssh_conn.run_commands([f"mkdir -p {remote_project_path}"])
        print(f"Syncing files to pod {project_pod_id} prod")
        ssh_conn.rsync(os.getcwd(), project_path_uuid_prod)

        # Create the virtual environment
        venv_path = os.path.join(project_path_uuid_prod, "venv")
        print(
            f"Activating Python virtual environment: {venv_path} on pod {project_pod_id}"
        )
        commands = [
            f'python{config["runtime"]["python_version"]} -m venv {venv_path}',
            f"source {venv_path}/bin/activate &&"
            f"cd {remote_project_path} &&"
            "python -m pip install --upgrade pip &&"
            f'python -m pip install -v --requirement {config["runtime"]["requirements_path"]}',
        ]
        ssh_conn.run_commands(commands)
        ssh_conn.close()

    environment_variables = {}
    for variable in config["project"]["env_vars"]:
        environment_variables[variable] = config["project"]["env_vars"][variable]

    # Construct the docker start command
    activate_cmd = (
        f'. /runpod-volume/{config["project"]["uuid"]}/prod/venv/bin/activate'
    )
    python_cmd = f'python -u /runpod-volume/{config["project"]["uuid"]}/prod/{config["project"]["name"]}/{config["runtime"]["handler_path"]}'  # pylint: disable=line-too-long
    docker_start_cmd = 'bash -c "' + activate_cmd + " && " + python_cmd + '"'

    project_endpoint_template = create_template(
        name=f'{config["project"]["name"]}-endpoint | {config["project"]["uuid"]} | {datetime.now()}',  # pylint: disable=line-too-long
        image_name=config["project"]["base_image"],
        container_disk_in_gb=config["project"]["container_disk_size_gb"],
        docker_start_cmd=docker_start_cmd,
        env=environment_variables,
        is_serverless=True,
    )

    deployed_endpoint = get_project_endpoint(config["project"]["uuid"])
    if not deployed_endpoint:
        deployed_endpoint = create_endpoint(
            name=f'{config["project"]["name"]}-endpoint | {config["project"]["uuid"]}',
            template_id=project_endpoint_template["id"],
            network_volume_id=config["project"]["storage_id"],
        )
    else:
        deployed_endpoint = update_endpoint_template(
            endpoint_id=deployed_endpoint["id"],
            template_id=project_endpoint_template["id"],
        )

    # does user want to tear down and recreate workers immediately?

    return deployed_endpoint["id"]
