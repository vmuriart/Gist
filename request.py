import functools
import json
import os
import shutil
import traceback

import sublime
import urllib.request as urllib


class MissingCredentialsException(Exception):
    pass


def token_auth_string():
    settings = sublime.load_settings('Gist.sublime-settings')
    token = settings.get('token')

    if not token:
        raise MissingCredentialsException()
    return token


def api_request(url, data=None, method=None):
    settings = sublime.load_settings('Gist.sublime-settings')
    request = urllib.Request(url)

    token = token_auth_string()
    https_proxy = settings.get('https_proxy')

    if https_proxy:
        opener = urllib.build_opener(urllib.HTTPHandler(), urllib.HTTPSHandler(),
                                     urllib.ProxyHandler({'https': https_proxy}))
        urllib.install_opener(opener)

    if method:
        request.get_method = lambda: method

    if data:
        data = json.dumps(data)
        request.add_data(bytes(data.encode('utf8')))

    request.add_header('Authorization', 'token ' + token)
    request.add_header('Accept', 'application/json')
    request.add_header('Content-Type', 'application/json')

    with urllib.urlopen(request) as response:
        if response.code == 204:  # No Content
            return None
        return json.loads(response.read().decode('utf8', 'ignore'))


def catch_errors(fn):
    @functools.wraps(fn)
    def _fn(*args, **kwargs):
        try:
            return fn(*args, **kwargs)

        except MissingCredentialsException:
            sublime.error_message("Gist: GitHub token isn't provided in Gist.sublime-settings file. "
                                  "All other authorization methods are deprecated.")
            user_settings_path = os.path.join(sublime.packages_path(), 'User', 'Gist.sublime-settings')

            if not os.path.exists(user_settings_path):
                default_settings_path = os.path.join(sublime.packages_path(), 'Gist', 'Gist.sublime-settings')
                shutil.copy(default_settings_path, user_settings_path)
            sublime.active_window().open_file(user_settings_path)

        except:
            traceback.print_exc()
            sublime.error_message("Gist: unknown error (please, report a bug!)")
    return _fn
