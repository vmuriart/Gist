import os
import tempfile
import threading
import webbrowser

import sublime
import sublime_plugin
from .helpers import gistify_view, set_syntax, gists_filter, ungistify_view
from .request import api_request, catch_errors

settings = sublime.load_settings('Gist.sublime-settings')


def plugin_loaded():
    global settings
    settings = sublime.load_settings('Gist.sublime-settings')

    if settings.get('max_gists') > 100:
        settings.set('max_gists', 100)
        sublime.status_message("Gist: GitHub API does not support a value of higher than 100")

    max_gists = '?per_page=%d' % settings.get('max_gists')

    api_url = settings.get('api_url')  # Should add validation?
    settings.set('GISTS_URL', api_url + '/gists' + max_gists)
    settings.set('USER_GISTS_URL', api_url + '/users/%s/gists' + max_gists)
    settings.set('STARRED_GISTS_URL', api_url + '/gists/starred' + max_gists)
    settings.set('ORGS_URL', api_url + '/user/orgs')
    settings.set('ORG_MEMBERS_URL', api_url + '/orgs/%s/members')


@catch_errors
def create_gist(public, description, files):
    for text in files.values():
        if not text:
            sublime.error_message("Gist: Unable to create a Gist with empty content")
            return

    file_data = {filename: {'content': text} for filename, text in files.items()}
    data = {'description': description, 'public': public, 'files': file_data}

    return api_request(settings.get('GISTS_URL'), data)


@catch_errors
def update_gist(gist_url, file_changes=None, new_description=None):
    file_changes = dict() if file_changes is None else file_changes
    data = {'files': file_changes}

    if new_description is not None:
        data['description'] = new_description

    result = api_request(gist_url, data, method="PATCH")
    sublime.status_message("Gist updated")

    return result


@catch_errors
def open_gist(gist_url):
    allowed_types = 'text', 'application'
    gist = api_request(gist_url)

    for filename, data in gist['files'].items():
        type_ = data['type'].split('/')[0]

        if type_ not in allowed_types:
            continue

        view = sublime.active_window().new_file()
        gistify_view(view, gist, filename)
        view.run_command('append', {'characters': data['content']})

        if settings.get('supress_save_dialog'):
            view.set_scratch(True)

        if settings.get('save_update_hook'):
            view.retarget(tempfile.gettempdir() + '/' + filename)
            # Save over it (to stop us reloading from that file in case it exists)
            # But don't actually do a gist update
            view.settings().set('do-update', False)
            view.run_command('save')

        set_syntax(view, data)


@catch_errors
def insert_gist(gist_url):
    gist = api_request(gist_url)

    for data in gist['files'].values():
        view = sublime.active_window().active_view()

        auto_indent = view.settings().get('auto_indent')
        view.settings().set('auto_indent', False)
        view.run_command('insert', {'characters': data['content']})
        view.settings().set('auto_indent', auto_indent)


@catch_errors
def insert_gist_embed(gist_url):
    gist = api_request(gist_url)

    for data in gist['files'].values():
        template = '<script src="{0}"></script>'.format(data['raw_url'])

        view = sublime.active_window().active_view()
        view.run_command('insert', {'characters': template})


class GistCommand(sublime_plugin.TextCommand):
    public = True

    def mode(self):
        return "Public" if self.public else "Private"

    def run(self, edit):
        regions = [region for region in self.view.sel() if not region.empty()]

        if len(regions) == 0:
            regions = [sublime.Region(0, self.view.size())]

        region_data = [self.view.substr(region) for region in regions]
        window = self.view.window()

        def on_gist_description(description):
            filename = os.path.basename(self.view.file_name() if self.view.file_name() else '')

            def on_gist_filename(filename):
                # We need to figure out the filenames. Right now, the following logic is used:
                #   If there's only 1 selection, just pass whatever the user typed to Github.
                #       It'll rename empty files for us.
                #   If there are multiple selections and user entered a filename, rename the files from foo.js to
                #       foo (1).js, foo (2).js, etc.
                #   If there are multiple selections and user didn't enter anything, post the files as
                #       $SyntaxName 1, $SyntaxName 2, etc.
                if len(region_data) == 1:
                    gist_data = {filename: region_data[0]}

                else:
                    if filename:
                        namepart, extpart = os.path.splitext(filename)
                        make_filename = lambda num: "%s (%d)%s" % (namepart, num, extpart)

                    else:
                        syntax_name, _ = os.path.splitext(os.path.basename(self.view.settings().get('syntax')))
                        make_filename = lambda num: "%s %d" % (syntax_name, num)
                    gist_data = {make_filename(idx): data for idx, data in enumerate(region_data, 1)}

                gist = create_gist(self.public, description, gist_data)

                if not gist:
                    return

                gist_html_url = gist['html_url']
                sublime.set_clipboard(gist_html_url)
                sublime.status_message("%s Gist: %s" % (self.mode(), gist_html_url))

                if regions:
                    gistify_view(self.view, gist, list(gist['files'].keys())[0])
                # else:
                    # open_gist(gist['url'])

            window.show_input_panel('Gist File Name: (optional):', filename, on_gist_filename, None, None)

        window.show_input_panel("Gist Description (optional):", '', on_gist_description, None, None)


class GistPrivateCommand(GistCommand):
    public = False


class GistViewCommand(object):
    """A base class for commands operating on a gistified view."""

    def is_enabled(self):
        return self.gist_url() is not None

    def gist_url(self):
        return self.view.settings().get("gist_url")

    def gist_html_url(self):
        return self.view.settings().get("gist_html_url")

    def gist_filename(self):
        return self.view.settings().get("gist_filename")

    def gist_description(self):
        return self.view.settings().get("gist_description")


class GistCopyUrl(GistViewCommand, sublime_plugin.TextCommand):
    def run(self, edit):
        sublime.set_clipboard(self.gist_html_url())


class GistOpenBrowser(GistViewCommand, sublime_plugin.TextCommand):
    def run(self, edit):
        webbrowser.open(self.gist_html_url())


class GistRenameFileCommand(GistViewCommand, sublime_plugin.TextCommand):
    def run(self, edit):
        old_filename = self.gist_filename()

        def on_filename(filename):
            if filename and filename != old_filename:
                text = self.view.substr(sublime.Region(0, self.view.size()))
                file_changes = {old_filename: {'filename': filename, 'content': text}}

                new_gist = update_gist(self.gist_url(), file_changes)
                gistify_view(self.view, new_gist, filename)
                sublime.status_message('Gist file renamed')

        self.view.window().show_input_panel('New File Name:', old_filename, on_filename, None, None)


class GistChangeDescriptionCommand(GistViewCommand, sublime_plugin.TextCommand):
    def run(self, edit):
        def on_gist_description(description):
            if description and description != self.gist_description():
                gist_url = self.gist_url()
                new_gist = update_gist(gist_url, new_description=description)

                for window in sublime.windows():
                    for view in window.views():
                        if view.settings().get('gist_url') == gist_url:
                            gistify_view(view, new_gist, view.settings().get('gist_filename'))
                sublime.status_message('Gist description changed')

        self.view.window().show_input_panel('New Description:', self.gist_description() or '',
                                            on_gist_description, None, None)


class GistUpdateFileCommand(GistViewCommand, sublime_plugin.TextCommand):
    def run(self, edit):
        text = self.view.substr(sublime.Region(0, self.view.size()))
        changes = {self.gist_filename(): {'content': text}}

        update_gist(self.gist_url(), changes)
        sublime.status_message("Gist updated")


class GistDeleteFileCommand(GistViewCommand, sublime_plugin.TextCommand):
    def run(self, edit):
        changes = {self.gist_filename(): None}

        update_gist(self.gist_url(), changes)
        ungistify_view(self.view)
        sublime.status_message("Gist file deleted")


class GistDeleteCommand(GistViewCommand, sublime_plugin.TextCommand):
    @catch_errors
    def run(self, edit):
        gist_url = self.gist_url()
        api_request(gist_url, method='DELETE')

        for window in sublime.windows():
            for view in window.views():
                if view.settings().get("gist_url") == gist_url:
                    ungistify_view(view)
        sublime.status_message("Gist deleted")


class GistListener(GistViewCommand, sublime_plugin.EventListener):
    def on_pre_save(self, view):
        if view.settings().get('gist_filename') is not None:
            if settings.get('save_update_hook'):
                # we ignore the first update, it happens upon loading a gist
                if not view.settings().get('do-update'):
                    view.settings().set('do-update', True)
                    return

                text = view.substr(sublime.Region(0, view.size()))
                changes = {view.settings().get('gist_filename'): {'content': text}}
                gist_url = view.settings().get('gist_url')
                # Start update_gist in a thread so we don't stall the save
                threading.Thread(target=update_gist, args=(gist_url, changes)).start()


class GistListCommandBase(object):
    gists = orgs = users = []

    @catch_errors
    def run(self, *args):
        filtered = gists_filter(api_request(settings.get('GISTS_URL')))
        filtered_stars = gists_filter(api_request(settings.get('STARRED_GISTS_URL')))

        self.gists = filtered[0] + filtered_stars[0]
        gist_names = filtered[1] + list(map(lambda x: [u"★ " + x[0]], filtered_stars[1]))

        if settings.get('include_users'):
            self.users = settings.get('include_users')
            gist_names = [["> " + user] for user in self.users] + gist_names

        if settings.get('include_orgs'):
            self.orgs = [org.get("login") for org in api_request(settings.get('ORGS_URL'))]
            gist_names = [["> " + org] for org in self.orgs] + gist_names

        def on_gist_num(num):
            off_orgs = len(self.orgs)
            off_users = off_orgs + len(self.users)

            if num < 0:
                pass

            elif num < off_orgs:
                self.gists = []

                members = [member.get("login") for member in
                           api_request(settings.get('ORG_MEMBERS_URL') % self.orgs[num])]
                for member in members:
                    self.gists += api_request(settings.get('USER_GISTS_URL') % member)

                filtered = gists_filter(self.gists)
                self.gists = filtered[0]
                gist_names = filtered[1]

                self.orgs = self.users = []
                self.get_window().show_quick_panel(gist_names, on_gist_num)

            elif num < off_users:
                filtered = gists_filter(api_request(settings.get('USER_GISTS_URL') % self.users[num - off_orgs]))
                self.gists = filtered[0]
                gist_names = filtered[1]

                self.orgs = self.users = []
                self.get_window().show_quick_panel(gist_names, on_gist_num)

            else:
                self.handle_gist(self.gists[num - off_users])

        self.get_window().show_quick_panel(gist_names, on_gist_num)


class GistListCommand(GistListCommandBase, sublime_plugin.WindowCommand):
    def handle_gist(self, gist):
        open_gist(gist['url'])

    def get_window(self):
        return self.window


class InsertGistListCommand(GistListCommandBase, sublime_plugin.WindowCommand):
    def handle_gist(self, gist):
        insert_gist(gist['url'])

    def get_window(self):
        return self.window


class InsertGistEmbedListCommand(GistListCommandBase, sublime_plugin.WindowCommand):
    def handle_gist(self, gist):
        insert_gist_embed(gist['url'])

    def get_window(self):
        return self.window


class GistAddFileCommand(GistListCommandBase, sublime_plugin.TextCommand):
    def is_enabled(self):
        return self.view.settings().get('gist_url') is None

    def handle_gist(self, gist):
        def on_filename(filename):
            if filename:
                text = self.view.substr(sublime.Region(0, self.view.size()))
                changes = {filename: {'content': text}}

                new_gist = update_gist(gist['url'], changes)
                gistify_view(self.view, new_gist, filename)
                sublime.status_message("File added to Gist")

        filename = os.path.basename(self.view.file_name() if self.view.file_name() else '')
        self.view.window().show_input_panel('File Name:', filename, on_filename, None, None)

    def get_window(self):
        return self.view.window()
