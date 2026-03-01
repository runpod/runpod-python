"""
Runpod | CLI | Pod | Commands
"""

import os
import tempfile
import uuid
import click
from prettytable import PrettyTable

from runpod import create_pod, get_pods

from ...utils import ssh_cmd


def _create_remote_archive(ssh_conn, source_workspace, archive_path):
    """Create a tar.gz archive of the source workspace on the remote pod.

    Returns the archive size string, or None if creation failed.
    """
    # Check if source directory exists
    _, stdout, _ = ssh_conn.ssh.exec_command(
        f"test -d {source_workspace} && echo 'exists' || echo 'not_found'"
    )
    if stdout.read().decode().strip() != "exists":
        click.echo(
            f"Error: Source workspace {source_workspace} does not exist on pod"
        )
        return None

    # Create tar.gz archive of the workspace
    click.echo(f"Creating archive of {source_workspace}...")
    tar_command = (
        f"cd {os.path.dirname(source_workspace)} && "
        f"tar -czf {archive_path} {os.path.basename(source_workspace)}"
    )
    ssh_conn.run_commands([tar_command])

    # Verify archive was created
    _, stdout, _ = ssh_conn.ssh.exec_command(
        f"test -f {archive_path} && echo 'created' || echo 'failed'"
    )
    if stdout.read().decode().strip() != "created":
        click.echo("Error: Failed to create archive on source pod")
        return None

    # Get archive size
    _, stdout, _ = ssh_conn.ssh.exec_command(f"du -h {archive_path} | cut -f1")
    return stdout.read().decode().strip()


def _download_to_local(ssh_conn, archive_path):
    """Download archive from remote pod to a local temp file.

    Returns the local temp file path.
    """
    click.echo("Downloading archive to local machine...")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as temp_file:
        local_temp_path = temp_file.name
        ssh_conn.get_file(archive_path, local_temp_path)

    # Clean up archive on source pod
    ssh_conn.run_commands([f"rm -f {archive_path}"])
    return local_temp_path


def _upload_and_extract(ssh_conn, local_temp_path, dest_workspace, dest_folder,
                        temp_zip_name):
    """Upload archive to destination pod and extract it.

    Returns the number of extracted files as a string.
    """
    ssh_conn.run_commands([f"mkdir -p {dest_workspace}"])

    click.echo("Uploading archive to destination pod...")
    dest_archive_path = f"/tmp/{temp_zip_name}"
    ssh_conn.put_file(local_temp_path, dest_archive_path)

    # Extract archive in destination workspace
    click.echo(f"Extracting archive to {dest_workspace}/{dest_folder}...")
    extract_command = (
        f"cd {dest_workspace} && mkdir -p {dest_folder} && "
        f"cd {dest_folder} && tar -xzf {dest_archive_path} --strip-components=1"
    )
    ssh_conn.run_commands([extract_command])

    # Verify extraction and count files
    _, stdout, _ = ssh_conn.ssh.exec_command(
        f"find {dest_workspace}/{dest_folder} -type f | wc -l"
    )
    dest_file_count = stdout.read().decode().strip()

    # Clean up archive on destination pod
    ssh_conn.run_commands([f"rm -f {dest_archive_path}"])
    return dest_file_count


@click.group("pod", help="Manage and interact with pods.")
def pod_cli():
    """A collection of CLI functions for Pod."""


@pod_cli.command("list")
def list_pods():
    """
    Lists the pods for the current user.
    """
    table = PrettyTable(["ID", "Name", "Status", "Image"])
    for pod in get_pods():
        table.add_row((pod["id"], pod["name"], pod["desiredStatus"], pod["imageName"]))

    click.echo(table)


@pod_cli.command("create")
@click.argument("name", required=False)
@click.option("--image", default=None, help="The image to use for the pod.")
@click.option("--gpu-type", default=None, help="The GPU type to use for the pod.")
@click.option("--gpu-count", default=1, help="The number of GPUs to use for the pod.")
@click.option(
    "--support-public-ip", default=True, help="Whether or not to support a public IP."
)
def create_new_pod(
    name, image, gpu_type, gpu_count, support_public_ip
):  # pylint: disable=too-many-arguments
    """
    Creates a pod.
    """
    kwargs = {
        "gpu_count": gpu_count,
        "support_public_ip": support_public_ip,
    }

    if not name:
        name = click.prompt("Enter pod name", default="RunPod-CLI-Pod")

    quick_launch = click.confirm("Would you like to launch default pod?", abort=True)
    if quick_launch:
        image = "runpod/base:0.0.0"
        gpu_type = "NVIDIA GeForce RTX 3090"
        kwargs["ports"] = "22/tcp"

        click.echo("Launching default pod...")

    new_pod = create_pod(name, image, gpu_type, **kwargs)

    click.echo(f'Pod {new_pod["id"]} has been created.')


@pod_cli.command("connect")
@click.argument("pod_id")
def connect_to_pod(pod_id):
    """
    Connects to a pod.
    """
    click.echo(f"Connecting to pod {pod_id}...")
    ssh = ssh_cmd.SSHConnection(pod_id)
    ssh.launch_terminal()


@pod_cli.command("sync")
@click.argument("source_pod_id")
@click.argument("dest_pod_id")
@click.argument("source_workspace", default="/workspace")
@click.argument("dest_workspace", default="/workspace")
def sync_pods(source_pod_id, dest_pod_id, source_workspace, dest_workspace):
    """
    Sync data between two pods via SSH.
    
    Transfers files from source_pod_id:source_workspace to dest_pod_id:dest_workspace.
    The workspace will be zipped and transferred to avoid file name conflicts.
    
    📋 PREREQUISITES:
    
    1. SSH Key Setup:
       • You must have an SSH key configured in your Runpod account
       • If you don't have one, create it with: runpod ssh add-key
       • List your keys with: runpod ssh list-keys
    
    2. Pod Configuration:
       • Both pods must have SSH access enabled
       • For running pods using official Runpod templates, you may need to add
         your public key to the PUBLIC_KEY environment variable and restart the pod
    
    ⚠️  IMPORTANT NOTES:
    
    • If a pod was started before adding your SSH key, you'll need to:
      1. Stop the pod
      2. Add PUBLIC_KEY environment variable with your public key
      3. Restart the pod
    
    • The sync creates a unique folder (sync_XXXXXXXX) in the destination to avoid
      file conflicts
    
    📖 EXAMPLES:
    
    Basic sync (uses /workspace as default):
        runpod pod sync pod1 pod2
    
    Custom paths:
        runpod pod sync pod1 pod2 /workspace/data /workspace/backup
    
    Different directories:
        runpod pod sync pod1 pod2 /home/user/files /workspace/imported
    """
    
    # Check if user has SSH keys configured
    try:
        from ...groups.ssh.functions import get_user_pub_keys
        user_keys = get_user_pub_keys()
        if not user_keys:
            click.echo("No SSH keys found in your Runpod account!")
            click.echo("")
            click.echo("To create an SSH key, run:")
            click.echo("   runpod ssh add-key")
            click.echo("")
            click.echo("For more help, see:")
            click.echo("   runpod ssh add-key --help")
            return
        else:
            click.echo(f"Found {len(user_keys)} SSH key(s) in your account")
    except Exception as e:
        click.echo(f"Warning: Could not verify SSH keys: {str(e)}")
        click.echo("Continuing with sync attempt...")

    click.echo(f"Syncing from {source_pod_id}:{source_workspace} to {dest_pod_id}:{dest_workspace}")

    # Generate unique folder name to avoid conflicts
    transfer_id = str(uuid.uuid4())[:8]
    temp_zip_name = f"sync_{transfer_id}.tar.gz"
    dest_folder = f"sync_{transfer_id}"
    local_temp_path = None

    try:
        # Connect to source pod and create archive
        click.echo(f"Connecting to source pod {source_pod_id}...")
        with ssh_cmd.SSHConnection(source_pod_id) as source_ssh:
            # Count files in source directory
            _, stdout, _ = source_ssh.ssh.exec_command(
                f"find {source_workspace} -type f | wc -l"
            )
            file_count = stdout.read().decode().strip()
            click.echo(f"Found {file_count} files in source workspace")

            archive_path = f"/tmp/{temp_zip_name}"
            archive_size = _create_remote_archive(
                source_ssh, source_workspace, archive_path
            )
            if archive_size is None:
                return

            click.echo(f"Archive created successfully ({archive_size})")
            local_temp_path = _download_to_local(source_ssh, archive_path)

        # Connect to destination pod, upload, and extract
        click.echo(f"Connecting to destination pod {dest_pod_id}...")
        with ssh_cmd.SSHConnection(dest_pod_id) as dest_ssh:
            dest_file_count = _upload_and_extract(
                dest_ssh, local_temp_path, dest_workspace, dest_folder, temp_zip_name
            )
            click.echo(f"Extracted {dest_file_count} files to destination")
            click.echo("")
            click.echo("Sync completed successfully!")
            click.echo(f"Files transferred: {file_count}")
            click.echo(f"Destination location: {dest_pod_id}:{dest_workspace}/{dest_folder}")
            click.echo("")
            click.echo("To access the synced files:")
            click.echo(f"   runpod ssh {dest_pod_id}")
            click.echo(f"   cd {dest_workspace}/{dest_folder}")

    except Exception as e:
        click.echo(f"Error during sync: {str(e)}")
        click.echo("")
        click.echo("Troubleshooting tips:")
        click.echo("- Ensure both pods have SSH access enabled")
        click.echo("- Check that your SSH key is added to your Runpod account: runpod ssh list-keys")
        click.echo("- For running pods, you may need to add PUBLIC_KEY env var and restart")
        click.echo("- Verify the source and destination paths exist")
    finally:
        # Clean up local temp file
        if local_temp_path:
            try:
                os.unlink(local_temp_path)
            except OSError:
                pass
