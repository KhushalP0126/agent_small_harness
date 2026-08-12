import socket

def connect(host, port):
    s = socket.socket()
    s.connect((host, port))
    return s
