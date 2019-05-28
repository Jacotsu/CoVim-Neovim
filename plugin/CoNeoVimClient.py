import neovim
import json
import warnings
from twisted.internet.protocol import ClientFactory, Protocol
from twisted.internet import reactor
from threading import Thread
from time import sleep


warnings.filterwarnings('ignore', '.*', UserWarning)
warnings.filterwarnings('ignore', '.*', DeprecationWarning)


def vim_print(vim_instance, message):
    vim_instance.command("echo \"{}\"".format(message))


# CoVim Protocol
class CoVimProtocol(Protocol):
    def __init__(self, fact, CoVim):
        self.fact = fact
        self.CoVim

    def send(self, event):
        self.transport.write(event.encode('utf-8'))

    def connectionMade(self):
        self.send(self.CoVim.username)

    def dataReceived(self, data_string):
        def clean_data_string(d_s):
            bad_data = d_s.find("}{")
            if bad_data > -1:
                d_s = d_s[:bad_data+1]
            return d_s

        if isinstance(data_string, bytes):
            data_string = data_string.decode('utf-8')

        data_string = clean_data_string(data_string)
        packet = json.loads(data_string)
        if 'packet_type' in packet.keys():
            data = packet['data']
            if packet['packet_type'] == 'message':
                if data['message_type'] == 'error_newname_taken':
                    self.fact.vim.async_call(self.CoVim.disconnect)
                    self.fact.vim.async_call(vim_print, self.vim, 'ERROR: Name already in use. Please try a different name')
                if data['message_type'] == 'error_newname_invalid':
                    self.fact.vim.async_call(self.CoVim.disconnect)
                    self.fact.vim.async_call(vim_print, self.vim, 'ERROR: Name contains illegal characters. Only numbers, letters, underscores, and dashes allowed. Please try a different name')
                if data['message_type'] == 'connect_success':
                    self.fact.vim.async_call(self.CoVim.setupWorkspace)
                    if 'buffer' in data.keys():
                        self.CoVim.vim_buffer = data['buffer']
                        self.vim.current.buffer[:] = self.CoVim.vim_buffer
                    self.fact.vim.async_call(self.CoVim.addUsers, data['collaborators'])
                    self.fact.vim.async_call(vim_print, self.vim, 'Success! You\'re now connected [Port '+str(self.CoVim.port)+']')
                if data['message_type'] == 'user_connected':
                    self.fact.vim.async_call(self.CoVim.addUsers, [data['user']])
                    self.fact.vim.async_call(vim_print, self.vim, data['user']['name']+' connected to this document')
                if data['message_type'] == 'user_disconnected':
                    self.fact.vim.async_call(self.CoVim.remUser, data['name'])
                    self.fact.vim.async_call(vim_print, self.vim, data['name']+' disconnected from this document')
            if packet['packet_type'] == 'update':
                if 'buffer' in data.keys() and data['name'] != self.CoVim.username:
                    b_data = data['buffer']
                    self.CoVim.vim_buffer = self.vim.current.buffer[:b_data['start']]   \
                                                         + b_data['buffer']   \
                                                         + self.vim.current.buffer[b_data['end']-b_data['change_y']+1:]
                    self.vim.current.buffer[:] = self.CoVim.vim_buffer
                if 'updated_cursors' in data.keys():
                    # We need to update your own cursor as soon as possible, then update other cursors after
                    for updated_user in data['updated_cursors']:
                        if self.CoVim.username == updated_user['name'] and \
                        data['name'] != self.CoVim.username:
                            self.vim.current.window.cursor = (updated_user['cursor']['y'], updated_user['cursor']['x'])
                    for updated_user in data['updated_cursors']:
                        if self.CoVim.username != updated_user['name']:
                            self.fact.vim.async_call(self.vim.command, ':call matchdelete(' + str(self.CoVim.collab_manager.collaborators[updated_user['name']][1]) + ')')
                            self.fact.vim.async_call(self.vim.command, ':call matchadd(\'' +
                                             self.CoVim.collab_manager.collaborators[updated_user['name']][0]
                                             + '\', \'\%' +
                                             str(updated_user['cursor']['x']) +
                                             'v.\%' +
                                             str(updated_user['cursor']['y']) +
                                             'l\', 10, ' + str(self.CoVim.collab_manager.collaborators[updated_user['name']][1]) + ')')
                #data['cursor']['x'] = max(1,data['cursor']['x'])
                #print(str(data['cursor']['x'])+', '+str(data['cursor']['y'])
            self.fact.vim.async_call(self.vim.command, ':redraw')


# CoVimFactory - Handles Socket Communication
class CoVimFactory(ClientFactory):

    def __init__(self, vim, CoVim):
        self.vim = vim
        self.CoVim = CoVim

    def buildProtocol(self, addr):
        self.p = CoVimProtocol(self, self.CoVim)
        return self.p

    def startFactory(self):
        self.isConnected = True

    def stopFactory(self):
        self.isConnected = False

    def buff_update(self):
        d = {
            "packet_type": "update",
            "data": {
                "cursor": {
                    "x": max(1, self.vim.current.window.cursor[1]),
                    "y": self.vim.current.window.cursor[0]
                },
                "name": self.CoVim.username
            }
        }
        d = self.create_update_packet(d)
        data = json.dumps(d)
        self.p.send(data)

    def cursor_update(self):
        d = {
            "packet_type": "update",
            "data": {
                "cursor": {
                    "x": max(1, self.vim.current.window.cursor[1]+1),
                    "y": self.vim.current.window.cursor[0]
                },
                "name": self.CoVim.username
            }
        }
        d = self.create_update_packet(d)
        data = json.dumps(d)
        self.p.send(data)

    def create_update_packet(self, d):
        current_buffer = self.vim.current.buffer[:]
        if current_buffer != self.CoVim.vim_buffer:
            cursor_y = self.vim.current.window.cursor[0] - 1
            change_y = len(current_buffer) - len(self.CoVim.vim_buffer)
            change_x = 0
            if len(self.CoVim.vim_buffer) > cursor_y-change_y and cursor_y-change_y >= 0 \
                and len(current_buffer) > cursor_y and cursor_y >= 0:
                change_x = len(current_buffer[cursor_y]) - len(self.CoVim.vim_buffer[cursor_y-change_y])
            limits = {
                'from': max(0, cursor_y-abs(change_y)),
                'to': min(len(self.vim.current.buffer)-1, cursor_y+abs(change_y))
            }
            d_buffer = {
                'start': limits['from'],
                'end': limits['to'],
                'change_y': change_y,
                'change_x': change_x,
                'buffer': self.vim.current.buffer[limits['from']:limits['to']+1],
                'buffer_size': len(current_buffer)
            }
            d['data']['buffer'] = d_buffer
            self.CoVim.vim_buffer = current_buffer
        return d

    def clientConnectionLost(self, connector, reason):
        # THIS IS A HACK
        if self.buddylist:
            self.vim.async_call(vim_print, self.vim, 'Lost connection')
            self.vim.async_call(self.CoVim.disconnect)

    def clientConnectionFailed(self, connector, reason):
        self.vim.async_call(vim_print, self.vim, 'Connection failed.')
        self.vim.async_call(self.CoVim.disconnect)


# Manage Collaborators
class CollaboratorManager:

    def __init__(self, vim, CoVim):
        self.vim = vim
        self.CoVim = CoVim
        self.collab_id_itr = 4
        self.reset()

    def reset(self):
        self.collab_color_itr = 1
        self.collaborators = {}
        self.buddylist_highlight_ids = []

    def addUser(self, user_obj):
        if user_obj['name'] == self.CoVim.username:
            self.collaborators[user_obj['name']] = ('CursorUser', 4000)
        else:
            self.collaborators[user_obj['name']] = ('Cursor' + str(self.collab_color_itr), self.collab_id_itr)
            self.collab_id_itr += 1
            self.collab_color_itr = (self.collab_id_itr-3) % 11
            self.vim.command(':call matchadd(\''+self.collaborators[user_obj['name']][0]+'\', \'\%' + str(user_obj['cursor']['x']) + 'v.\%'+str(user_obj['cursor']['y'])+'l\', 10, ' + str(self.collaborators[user_obj['name']][1]) + ')')
        self.refreshCollabDisplay()

    def remUser(self, name):
        self.vim.command('call matchdelete('+str(self.collaborators[name][1]) + ')')
        del(self.collaborators[name])
        self.refreshCollabDisplay()

    def refreshCollabDisplay(self):
        buddylist_window_width = int(self.vim.eval('winwidth(0)'))
        self.CoVim.buddylist[:] = ['']
        x_a = 1
        line_i = 0
        self.vim.command("1wincmd w")
        for match_id in self.buddylist_highlight_ids:
            self.vim.command('call matchdelete('+str(match_id) + ')')
        self.buddylist_highlight_ids = []
        for name in self.collaborators.keys():
            x_b = x_a + len(name)
            if x_b > buddylist_window_width:
                line_i += 1
                x_a = 1
                x_b = x_a + len(name)
                self.CoVim.buddylist.append('')
                self.vim.command('resize '+str(line_i+1))
            self.CoVim.buddylist[line_i] += name+' '
            self.buddylist_highlight_ids.append(self.vim.eval('matchadd(\''+self.collaborators[name][0]+'\',\'\%<'+str(x_b)+'v.\%>'+str(x_a)+'v\%'+str(line_i+1)+'l\',10,'+str(self.collaborators[name][1]+2000)+')'))
            x_a = x_b + 1
        self.vim.command("wincmd p")


# Manage all of CoVim
class CoVimScope:
    def __init__(self, vim):
        self.vim = vim
        self.addr = None
        self.port = None
        self.username = None
        self.vim_buffer = None
        self.fact = None
        self.collab_manager = None
        self.connection = None
        self.reactor_thread = None
        self.buddylist = None
        self.buddylist_window = None

    def initiate(self,
                 addr='localhost',
                 port=None,
                 name=None):
        default_name = self.vim.eval('CoVim_default_name')
        default_port = self.vim.eval('CoVim_default_port')
        if not port:
            if default_port != '0':
                port = default_port
            if self.port:
                port = self.port
        if not addr and self.addr:
            addr = self.addr
        if not name and default_name != '0':
            name = default_name

        if not addr or not port or not name:
            vim_print(self.vim,
                      'Syntax Error: Use form :CoVim connect <server address> <port> <name>')
            return
        # Check if connected. If connected, throw error.
        if self.fact and self.fact.isConnected:
            vim_print(self.vim, 'ERROR: Already connected. Please disconnect first')
            return

        if not self.connection:
            self.addr = str(addr)
            self.port = int(port)
            self.username = name
            self.vim_buffer = []
            self.fact = CoVimFactory(self.vim, self)
            self.collab_manager = CollaboratorManager(self.vim, self)
            self.connection = reactor.connectTCP(self.addr,
                                                 self.port,
                                                 self.fact)
            self.reactor_thread = Thread(target=reactor.run, args=(False,))
            self.reactor_thread.start()
            vim_print(self.vim, 'Connecting...')
        elif (int(port) != self.port) or (str(addr) != self.addr):
            vim_print(self.vim, 'ERROR: Different address/port already used. To try another, you need to restart Vim')
        else:
            self.collab_manager.reset()
            self.connection.connect()
            vim_print(self.vim, 'Reconnecting...')

    def createServer(self,
                     port=None,
                     name=None):
        default_name = self.vim.eval('CoVim_default_name')
        default_port = self.vim.eval('CoVim_default_port')

        if not port and default_port != '0':
            port = default_port
        if not name and default_name != '0':
            name = default_name

        CoVimServerPath = '~/.vim/bundle/CoVim/plugin/CoVimServer.py'
        self.vim.command(':silent execute "!{} {}'
                         ' &>/dev/null &"'.format(CoVimServerPath,
                                                  port))
        sleep(0.5)
        self.initiate('localhost', port, name)

    def setupWorkspace(self):
        self.vim.command('call SetCoVimColors()')
        self.vim.command("1new +setlocal\ stl=%!'CoVim-Collaborators'")
        self.buddylist = self.vim.current.buffer
        self.buddylist_window = self.vim.current.window
        self.vim.command("wincmd j")

    def addUsers(self, userlist):
        for user in userlist:
            self.collab_manager.addUser(user)

    def remUser(self, name):
        self.collab_manager.remUser(name)

    def refreshCollabDisplay(self):
        self.collab_manager.refreshCollabDisplay()

    def exit(self):
        if self.buddylist_window and self.connection:
            self.disconnect()
            self.vim.command('q')
        else:
            vim_print(self.vim, "ERROR: CoVim must be running to use this command")

    def disconnect(self):
        if self.buddylist:
            self.vim.command("1wincmd w")
            self.vim.command("q!")
            self.collab_manager.buddylist_highlight_ids = []
            for name, item in filter(lambda x, y: x != self.username,
                                     self.collab_manager
                                     .collaborators.items()):
                self.vim.command(':call matchdelete({})'.format(item[1]))
        self.buddylist = None
        self.buddylist_window = None
        if self.connection:
            self.connection.disconnect()
            vim_print(self.vim, 'Successfully disconnected from document!')
        else:
            vim_print(self.vim, "ERROR: CoVim must be running to use this command")

    def quit(self):
        reactor.callFromThread(reactor.stop)
        vim_print(self.vim, "Quit CoVim")


@neovim.plugin
class Main(object):
    def __init__(self, vim):
        self.vim = vim
        self.CoVim = CoVimScope(vim)

    @neovim.autocmd('CursorMoved')
    def cursor_update(self):
        if self.CoVim.fact:
            self.vim.async_call(self.CoVim.fact.cursor_update)

    @neovim.autocmd('CursorMovedI')
    def buffer_update(self):
        if self.CoVim.fact:
            self.vim.async_call(self.CoVim.fact.buff_update)

    @neovim.autocmd('VimLeave')
    def quit(self):
        if self.CoVim.reactor_thread:
            self.CoVim.quit()

    @neovim.command('CoVim', nargs='*')
    def covim_command(self, args):
        default_name = self.vim.eval('CoVim_default_name')
        default_port = self.vim.eval('CoVim_default_port')
        default_name_string = " - default: '{}'".format(default_name
                                                        if default_name != '0'
                                                        else "")
        default_port_string = " - default: '{}'".format(default_port if
                                                        default_port != '0'
                                                        else "")
        try:
            if args[0] == "connect":
                try:
                    self.vim.async_call(lambda: self.CoVim.initiate(*args[1:]))
                except ValueError:
                    vim_print(self.vim, "usage :CoVim connect [host address / 'localhost'] [port"+default_port_string+"] [name"+default_name_string+"]")
            elif args[0] == "disconnect":
                self.vim.async_call(lambda:
                                    self.CoVim.disconnect())
            elif args[0] == "quit":
                self.vim.async_call(lambda:
                                    self.CoVim.exit())
            elif args[0] == "start":
                try:
                    self.vim.async_call(lambda:
                                        self.CoVim.createServer(*args[1:]))
                except ValueError:
                    vim_print(self.vim, "usage :CoVim start [port"+default_port_string+"] [name"+default_name_string+"]")
            else:
                vim_print(self.vim, "usage: CoVim [start] [connect] [disconnect] [quit]")
        except IndexError:
            vim_print(self.vim, "usage: CoVim [start] [connect] [disconnect] [quit]")
