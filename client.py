import uuid
import grpc
import platform
import subprocess
import logging
import os
import getpass
import threading
import queue
import time
from terminal_proto import remote_shell_pb2
from terminal_proto import remote_shell_pb2_grpc

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RemoteShellClient:
    def __init__(self):
        self.running = True
        self.command_queue = queue.Queue()
        self.client_id = str(uuid.uuid4())  # Add this line

    def get_system_info(self):
        return remote_shell_pb2.ClientInfo(
            hostname=platform.node(),
            os_info=platform.system(),
            username=getpass.getuser(), 
            client_id=self.client_id
        )

    def execute_command(self, command):
        try:
            # Create process with pipe for real-time output
            process = subprocess.Popen(
                command.command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            # Read output and error in real-time
            stdout_data = []
            stderr_data = []
            
            while True:
                stdout_line = process.stdout.readline()
                stderr_line = process.stderr.readline()
                
                if stdout_line:
                    stdout_data.append(stdout_line)
                if stderr_line:
                    stderr_data.append(stderr_line)
                    
                if process.poll() is not None and not stdout_line and not stderr_line:
                    break
            
            # Get the final output
            stdout_output = ''.join(stdout_data)
            stderr_output = ''.join(stderr_data)
            
            return remote_shell_pb2.CommandResult(
                command_id=command.command_id,
                client_id=self.client_id,  # Make sure to set this in __init__
                output=stdout_output + stderr_output,
                exit_code=process.returncode,
                error=""
            )
            
        except subprocess.TimeoutExpired:
            return remote_shell_pb2.CommandResult(
                command_id=command.command_id,
                client_id=self.client_id,
                output="",
                exit_code=1,
                error=f"Command timed out after {command.timeout} seconds"
            )
        except Exception as e:
            return remote_shell_pb2.CommandResult(
                command_id=command.command_id,
                client_id=self.client_id,
                output="",
                exit_code=1,
                error=str(e)
            )

    def run(self):
        while self.running:
            try:
                with grpc.insecure_channel('localhost:50051') as channel:
                    stub = remote_shell_pb2_grpc.RemoteShellStub(channel)
                    
                    # Start command receiver thread
                    command_stream = stub.ConnectClient(self.get_system_info())
                    
                    for command in command_stream:
                        result = self.execute_command(command)
                        print(result)
                        try:
                            # Send the result back to the server
                            ack = stub.SendCommandResult(result)
                            if not ack.received:
                                logger.error("Server did not acknowledge command result")
                        except grpc.RpcError as e:
                            logger.error(f"Failed to send command result: {str(e)}")
                            
            except grpc.RpcError as e:
                logger.error(f"RPC error: {str(e)}")
                time.sleep(5)  # Wait before reconnecting
                
            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}")
                time.sleep(5)  # Wait before reconnecting

if __name__ == '__main__':
    client = RemoteShellClient()
    client.run()