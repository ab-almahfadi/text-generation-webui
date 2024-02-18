import argparse
import glob
import hashlib
import os
import platform
import re
import signal
import site
import subprocess
import sys

script_dir = os.getcwd()
conda_env_path = os.path.join(script_dir, "installer_files", "env")

# Remove the '# ' from the following lines as needed for your AMD GPU on Linux
# os.environ["ROCM_PATH"] = '/opt/rocm'
# os.environ["HSA_OVERRIDE_GFX_VERSION"] = '10.3.0'
# os.environ["HCC_AMDGPU_TARGET"] = 'gfx1030'

# Command-line flags
cmd_flags_path = os.path.join(script_dir, "CMD_FLAGS.txt")
if os.path.exists(cmd_flags_path):
    with open(cmd_flags_path, 'r') as f:
        CMD_FLAGS = ' '.join(line.strip().rstrip('\\').strip() for line in f if line.strip().rstrip('\\').strip() and not line.strip().startswith('#'))
else:
    CMD_FLAGS = ''

flags = f"{' '.join([flag for flag in sys.argv[1:] if flag != '--update'])} {CMD_FLAGS}"


def signal_handler(sig, frame):
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)


def is_linux():
    return sys.platform.startswith("linux")


def is_windows():
    return sys.platform.startswith("win")


def is_macos():
    return sys.platform.startswith("darwin")


def is_x86_64():
    return platform.machine() == "x86_64"


def cpu_has_avx2():
    try:
        import cpuinfo

        info = cpuinfo.get_cpu_info()
        if 'avx2' in info['flags']:
            return True
        else:
            return False
    except:
        return True


def cpu_has_amx():
    try:
        import cpuinfo

        info = cpuinfo.get_cpu_info()
        if 'amx' in info['flags']:
            return True
        else:
            return False
    except:
        return True


def torch_version():
    site_packages_path = None
    for sitedir in site.getsitepackages():
        if "site-packages" in sitedir and conda_env_path in sitedir:
            site_packages_path = sitedir
            break

    if site_packages_path:
        torch_version_file = open(os.path.join(site_packages_path, 'torch', 'version.py')).read().splitlines()
        torver = [line for line in torch_version_file if '__version__' in line][0].split('__version__ = ')[1].strip("'")
    else:
        from torch import __version__ as torver

    return torver


def is_installed():
    site_packages_path = None
    for sitedir in site.getsitepackages():
        if "site-packages" in sitedir and conda_env_path in sitedir:
            site_packages_path = sitedir
            break

    if site_packages_path:
        return os.path.isfile(os.path.join(site_packages_path, 'torch', '__init__.py'))
    else:
        return os.path.isdir(conda_env_path)


def check_env():
    # If we have access to conda, we are probably in an environment
    conda_exist = run_cmd("conda", environment=True, capture_output=True).returncode == 0
    if not conda_exist:
        print("Conda is not installed. Exiting...")
        sys.exit(1)

    # Ensure this is a new environment and not the base environment
    if os.environ["CONDA_DEFAULT_ENV"] == "base":
        print("Create an environment for this project and activate it. Exiting...")
        sys.exit(1)


def clear_cache():
    run_cmd("conda clean -a -y", environment=True)
    run_cmd("python -m pip cache purge", environment=True)


def print_big_message(message):
    message = message.strip()
    lines = message.split('\n')
    print("\n\n*******************************************************************")
    for line in lines:
        if line.strip() != '':
            print("*", line)

    print("*******************************************************************\n\n")


def calculate_file_hash(file_path):
    p = os.path.join(script_dir, file_path)
    if os.path.isfile(p):
        with open(p, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    else:
        return ''


def run_cmd(cmd, assert_success=False, environment=False, capture_output=False, env=None):
    # Use the conda environment
    if environment:
        if is_windows():
            conda_bat_path = os.path.join(script_dir, "installer_files", "conda", "condabin", "conda.bat")
            cmd = f'"{conda_bat_path}" activate "{conda_env_path}" >nul && {cmd}'
        else:
            conda_sh_path = os.path.join(script_dir, "installer_files", "conda", "etc", "profile.d", "conda.sh")
            cmd = f'. "{conda_sh_path}" && conda activate "{conda_env_path}" && {cmd}'

    # Run shell commands
    result = subprocess.run(cmd, shell=True, capture_output=capture_output, env=env)

    # Assert the command ran successfully
    if assert_success and result.returncode != 0:
        print(f"Command '{cmd}' failed with exit status code '{str(result.returncode)}'.\n\nExiting now.\nTry running the start/update script again.")
        sys.exit(1)

    return result


def install_webui():
    # Select your GPU, or choose to run in CPU mode
    if "GPU_CHOICE" in os.environ:
        choice = os.environ["GPU_CHOICE"].upper()
        print_big_message(f"Selected GPU choice \"{choice}\" based on the GPU_CHOICE environment variable.")
    else:
        print()
        print("What is your GPU?")
        print()
        print("A) NVIDIA")
        print("B) AMD (Linux/MacOS only. Requires ROCm SDK 5.6 on Linux)")
        print("C) Apple M Series")
        print("D) Intel Arc (IPEX)")
        print("N) None (I want to run models in CPU mode)")
        print()

        choice = input("Input> ").upper()
        while choice not in 'ABCDN':
            print("Invalid choice. Please try again.")
            choice = input("Input> ").upper()

    gpu_choice_to_name = {
        "A": "NVIDIA",
        "B": "AMD",
        "C": "APPLE",
        "D": "INTEL",
        "N": "NONE"
    }

    selected_gpu = gpu_choice_to_name["A"]

    if selected_gpu == "NONE":
        with open(cmd_flags_path, 'r+') as cmd_flags_file:
            if "--cpu" not in cmd_flags_file.read():
                print_big_message("Adding the --cpu flag to CMD_FLAGS.txt.")
                cmd_flags_file.write("\n--cpu")

    # Find the proper Pytorch installation command
    install_git = "conda install -y -k ninja git"
    install_pytorch = "python -m pip install torch==2.1.* torchvision==0.16.* torchaudio==2.1.* "

    use_cuda118 = "N"
    if any((is_windows(), is_linux())) and selected_gpu == "NVIDIA":
        if "USE_CUDA118" in os.environ:
            use_cuda118 = "Y" if os.environ.get("USE_CUDA118", "").lower() in ("yes", "y", "true", "1", "t", "on") else "N"
        else:
            # Ask for CUDA version if using NVIDIA
            print("\nDo you want to use CUDA 11.8 instead of 12.1? Only choose this option if your GPU is very old (Kepler or older).\nFor RTX and GTX series GPUs, say \"N\". If unsure, say \"N\".\n")
            use_cuda118 = input("Input (Y/N)> ").upper().strip('"\'').strip()
            while use_cuda118 not in 'YN':
                print("Invalid choice. Please try again.")
                use_cuda118 = input("Input> ").upper().strip('"\'').strip()

        if use_cuda118 == 'Y':
            print("CUDA: 11.8")
            install_pytorch += "--index-url https://download.pytorch.org/whl/cu118"
        else:
            print("CUDA: 12.1")
            install_pytorch += "--index-url https://download.pytorch.org/whl/cu121"
    elif not is_macos() and selected_gpu == "AMD":
        if is_linux():
            install_pytorch += "--index-url https://download.pytorch.org/whl/rocm5.6"
        else:
            print("AMD GPUs are only supported on Linux. Exiting...")
            sys.exit(1)
    elif is_linux() and selected_gpu in ["APPLE", "NONE"]:
        install_pytorch += "--index-url https://download.pytorch.org/whl/cpu"
    elif selected_gpu == "INTEL":
        install_pytorch = "python -m pip install torch==2.1.0a0 torchvision==0.16.0a0 torchaudio==2.1.0a0 intel-extension-for-pytorch==2.1.10 --extra-index-url https://pytorch-extension.intel.com/release-whl/stable/xpu/us/"

    # Install Git and then Pytorch
    print_big_message("Installing PyTorch.")
    run_cmd(f"{install_git} && {install_pytorch} && python -m pip install py-cpuinfo==9.0.0", assert_success=True, environment=True)

    # Install CUDA libraries (this wasn't necessary for Pytorch before...)
    if selected_gpu == "NVIDIA":
        print_big_message("Installing the CUDA runtime libraries.")
        run_cmd(f"conda install -y -c \"nvidia/label/{'cuda-12.1.1' if use_cuda118 == 'N' else 'cuda-11.8.0'}\" cuda-runtime", assert_success=True, environment=True)

    if selected_gpu == "INTEL":
        # Install oneAPI dependencies via conda
        print_big_message("Installing Intel oneAPI runtime libraries.")
        run_cmd("conda install -y -c intel dpcpp-cpp-rt=2024.0 mkl-dpcpp=2024.0")
        # Install libuv required by Intel-patched torch
        run_cmd("conda install -y libuv")

    # Install the webui requirements
    update_requirements(initial_installation=True)


def update_requirements(initial_installation=False):
    # Create .git directory if missing
    if not os.path.exists(os.path.join(script_dir, ".git")):
        git_creation_cmd = 'git init -b main && git remote add origin https://github.com/oobabooga/text-generation-webui && git fetch && git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main && git reset --hard origin/main && git branch --set-upstream-to=origin/main'
        run_cmd(git_creation_cmd, environment=True, assert_success=True)

    files_to_check = [
        'start_linux.sh', 'start_macos.sh', 'start_windows.bat', 'start_wsl.bat',
        'update_linux.sh', 'update_macos.sh', 'update_windows.bat', 'update_wsl.bat',
        'one_click.py'
    ]

    before_pull_hashes = {file_name: calculate_file_hash(file_name) for file_name in files_to_check}
    run_cmd("git pull --autostash", assert_success=True, environment=True)
    after_pull_hashes = {file_name: calculate_file_hash(file_name) for file_name in files_to_check}

    # Check for differences in installation file hashes
    for file_name in files_to_check:
        if before_pull_hashes[file_name] != after_pull_hashes[file_name]:
            print_big_message(f"File '{file_name}' was updated during 'git pull'. Please run the script again.")
            exit(1)

    # Extensions requirements are installed only during the initial install by default.
    # That can be changed with the INSTALL_EXTENSIONS environment variable.
    install = initial_installation
    if "INSTALL_EXTENSIONS" in os.environ:
        install = os.environ["INSTALL_EXTENSIONS"].lower() in ("yes", "y", "true", "1", "t", "on")

    if install:
        print_big_message("Installing extensions requirements.")
        skip = ['superbooga', 'superboogav2', 'coqui_tts']  # Fail to install on Windows
        extensions = [foldername for foldername in os.listdir('extensions') if os.path.isfile(os.path.join('extensions', foldername, 'requirements.txt'))]
        extensions = [x for x in extensions if x not in skip]
        for i, extension in enumerate(extensions):
            print(f"\n\n--- [{i+1}/{len(extensions)}]: {extension}\n\n")
            extension_req_path = os.path.join("extensions", extension, "requirements.txt")
            run_cmd(f"python -m pip install -r {extension_req_path} --upgrade", assert_success=False, environment=True)
    elif initial_installation:
        print_big_message("Will not install extensions due to INSTALL_EXTENSIONS environment variable.")

    # Detect the Python and PyTorch versions
    torver = torch_version()
    is_cuda = '+cu' in torver
    is_cuda118 = '+cu118' in torver  # 2.1.0+cu118
    is_rocm = '+rocm' in torver  # 2.0.1+rocm5.4.2
    is_intel = '+cxx11' in torver  # 2.0.1a0+cxx11.abi
    is_cpu = '+cpu' in torver  # 2.0.1+cpu

    if is_rocm:
        base_requirements = "requirements_amd" + ("_noavx2" if not cpu_has_avx2() else "") + ".txt"
    elif is_cpu or is_intel:
        base_requirements = "requirements_cpu_only" + ("_noavx2" if not cpu_has_avx2() else "") + ".txt"
    elif is_macos():
        base_requirements = "requirements_apple_" + ("intel" if is_x86_64() else "silicon") + ".txt"
    else:
        base_requirements = "requirements" + ("_noavx2" if not cpu_has_avx2() else "") + ".txt"

    requirements_file = base_requirements

    print_big_message(f"Installing webui requirements from file: {requirements_file}")
    print(f"TORCH: {torver}\n")

    # Prepare the requirements file
    textgen_requirements = open(requirements_file).read().splitlines()
    if is_cuda118:
        textgen_requirements = [req.replace('+cu121', '+cu118').replace('+cu122', '+cu118') for req in textgen_requirements]
    if is_windows() and is_cuda118:  # No flash-attention on Windows for CUDA 11
        textgen_requirements = [req for req in textgen_requirements if 'jllllll/flash-attention' not in req]

    with open('temp_requirements.txt', 'w') as file:
        file.write('\n'.join(textgen_requirements))

    # Workaround for git+ packages not updating properly.
    git_requirements = [req for req in textgen_requirements if req.startswith("git+")]
    for req in git_requirements:
        url = req.replace("git+", "")
        package_name = url.split("/")[-1].split("@")[0].rstrip(".git")
        run_cmd(f"python -m pip uninstall -y {package_name}", environment=True)
        print(f"Uninstalled {package_name}")

    # Make sure that API requirements are installed (temporary)
    extension_req_path = os.path.join("extensions", "openai", "requirements.txt")
    if os.path.exists(extension_req_path):
        run_cmd(f"python -m pip install -r {extension_req_path} --upgrade", environment=True)

    # Install/update the project requirements
    run_cmd("python -m pip install -r temp_requirements.txt --upgrade", assert_success=True, environment=True)
    os.remove('temp_requirements.txt')

    # Check for '+cu' or '+rocm' in version string to determine if torch uses CUDA or ROCm. Check for pytorch-cuda as well for backwards compatibility
    if not any((is_cuda, is_rocm)) and run_cmd("conda list -f pytorch-cuda | grep pytorch-cuda", environment=True, capture_output=True).returncode == 1:
        clear_cache()
        return

    if not os.path.exists("repositories/"):
        os.mkdir("repositories")

    clear_cache()


def launch_webui():
    print(f"python server.py {flags}")
    run_cmd(f"python server.py {flags}", environment=True)


if __name__ == "__main__":
    # Verifies we are in a conda environment
    check_env()

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--update', action='store_true', help='Update the web UI.')
    args, _ = parser.parse_known_args()

    if args.update:
        update_requirements()
    else:
        # If webui has already been installed, skip and run
        if not is_installed():
            install_webui()
            os.chdir(script_dir)

        if os.environ.get("LAUNCH_AFTER_INSTALL", "").lower() in ("no", "n", "false", "0", "f", "off"):
            print_big_message("Install finished successfully and will now exit due to LAUNCH_AFTER_INSTALL.")
            sys.exit()

        # Check if a model has been downloaded yet
        if '--model-dir' in flags:
            # Splits on ' ' or '=' while maintaining spaces within quotes
            flags_list = re.split(' +(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)|=', flags)
            model_dir = [flags_list[(flags_list.index(flag) + 1)] for flag in flags_list if flag == '--model-dir'][0].strip('"\'')
        else:
            model_dir = 'models'

        if len([item for item in glob.glob(f'{model_dir}/*') if not item.endswith(('.txt', '.yaml'))]) == 0:
            print_big_message("WARNING: You haven't downloaded any model yet.\nOnce the web UI launches, head over to the \"Model\" tab and download one.")

        # Workaround for llama-cpp-python loading paths in CUDA env vars even if they do not exist
        conda_path_bin = os.path.join(conda_env_path, "bin")
        if not os.path.exists(conda_path_bin):
            os.mkdir(conda_path_bin)

        # # Launch the webui
        launch_webui()
