import grpc
from concurrent import futures
import logging
import uuid
import threading
import queue
import platform
import sys
import datetime
from prompt_toolkit import PromptSession
from prompt_toolkit.shortcuts import prompt
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import FormattedText

from terminal_proto import remote_shell_pb2
from terminal_proto import remote_shell_pb2_grpc

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ClientSession:
    def __init__(self, client_info, command_queue):
        self.client_id = client_info.client_id
        self.hostname = client_info.hostname
        self.os_info = client_info.os_info
        self.username = client_info.username
        self.command_queue = command_queue
        self.last_seen = datetime.datetime.now()
        self.active = True

class RemoteShellServicer(remote_shell_pb2_grpc.RemoteShellServicer):
    def __init__(self):
        self.clients = {}  # client_id -> ClientSession
        self.command_results = {}  # command_id -> result
        self.current_client = None
        self.prompt_style = Style.from_dict({
            'username': '#00aa00 bold',
            'hostname': '#00aa00',
            'path': '#0000aa',
        })

    def ConnectClient(self, request, context):
        client_session = ClientSession(request, queue.Queue())
        self.clients[client_session.client_id] = client_session
        logger.info(f"New client connected - ID: {request.client_id}, "
                    f"Hostname: {request.hostname}, OS: {request.os_info}")

        try:
            while client_session.active:
                try:
                    # Wait for commands from the server console
                    command = client_session.command_queue.get(timeout=1)
                    if command:
                        yield command
                except queue.Empty:
                    continue  # Keep the stream alive
                except Exception as e:
                    logger.error(f"Error in command stream: {str(e)}")
                    break
        finally:
            if client_session.client_id in self.clients:
                del self.clients[client_session.client_id]
            logger.info(f"Client disconnected: {client_session.client_id}")

    def SendCommandResult(self, request, context):
        client_id = request.client_id
        print("CLIENT ID", client_id)
        if client_id in self.clients:
            self.command_results[request.command_id] = request
            # Print the result to the server console
            if request.error:
                print(f"Error: {request.error}")
            else:
                print(request.output)
            if request.exit_code != 0:
                print(f"Exit code: {request.exit_code}")
        return remote_shell_pb2.CommandAck(received=True)

class ServerShell:
    def __init__(self, servicer):
        self.servicer = servicer
        self.session = PromptSession(refresh_interval=None)
        self.running = True

    def get_prompt_text(self):
        if not self.servicer.current_client:
            return FormattedText([
                ('class:username', 'server'),
                ('', '> '),
            ])
        
        client = self.servicer.clients.get(self.servicer.current_client)
        if not client:
            return FormattedText([('', '> ')])
            
        return FormattedText([
            ('class:username', client.username),
            ('', '@'),
            ('class:hostname', client.hostname),
            ('', '> '),
        ])

    def handle_server_commands(self):
        while self.running:
            try:
                command = self.session.prompt(self.get_prompt_text, style=self.servicer.prompt_style)
                
                if not command:
                    continue
                    
                if command == "exit":
                    self.running = False
                    break
                    
                if command == "clients":
                    self.list_clients()
                    continue
                    
                if command.startswith("use "):
                    self.select_client(command[4:])
                    continue
                    
                if self.servicer.current_client:
                    self.send_command_to_client(command)
                else:
                    print("No client selected. Use 'clients' to list available clients and 'use <hostname>' to select one.")
                    
            except KeyboardInterrupt:
                continue
            except EOFError:
                break
            except Exception as e:
                logger.error(f"Error in server shell: {str(e)}")

    def list_clients(self):
        if not self.servicer.clients:
            print("No clients connected.")
            return
            
        print("\nConnected clients:")
        print("ID\t\t\t\tHostname\t\tOS\t\t\tUsername")
        print("-" * 80)
        for client_id, session in self.servicer.clients.items():
            print(f"{client_id[:8]}...\t{session.hostname}\t\t{session.os_info}\t\t{session.username}")
        print()

    def select_client(self, identifier):
        # Try to match by hostname or ID
        for client_id, session in self.servicer.clients.items():
            if session.hostname == identifier or client_id.startswith(identifier):
                self.servicer.current_client = client_id
                print(f"Selected client: {session.hostname} ({client_id[:8]}...)")
                return
        print(f"No client found matching '{identifier}'")

    def send_command_to_client(self, command):
        client = self.servicer.clients.get(self.servicer.current_client)
        if not client:
            print("Selected client is no longer connected.")
            self.servicer.current_client = None
            return

        command_id = str(uuid.uuid4())
        command_pb = remote_shell_pb2.Command(
            command_id=command_id,
            command=command,
            timeout=30
        )
        client.command_queue.put(command_pb)

def serve():
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        options=[
            ('grpc.max_send_message_length', 1024 * 1024 * 10),
            ('grpc.max_receive_message_length', 1024 * 1024 * 10),
            ('grpc.keepalive_time_ms', 10000),
            ('grpc.keepalive_timeout_ms', 5000)
        ]
    )
    
    servicer = RemoteShellServicer()
    remote_shell_pb2_grpc.add_RemoteShellServicer_to_server(servicer, server)
    server.add_insecure_port('[::]:50051')
    server.start()
    
    shell = ServerShell(servicer)
    print("Server started on port 50051")
    print("Available commands:")
    print("  clients         - List connected clients")
    print("  use <hostname>  - Select a client to send commands to")
    print("  exit           - Exit the server")
    print("  <command>      - Send command to selected client")
    
    shell.handle_server_commands()
    server.stop(0)

if __name__ == '__main__':
    serve()