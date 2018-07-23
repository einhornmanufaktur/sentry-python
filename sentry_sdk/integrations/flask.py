from __future__ import absolute_import

from sentry_sdk import capture_exception, configure_scope, get_current_hub
from ._wsgi import get_environ

try:
    from flask_login import current_user
except ImportError:
    current_user = None

from flask import request, _app_ctx_stack
from flask.signals import appcontext_pushed, got_request_exception


class FlaskSentry(object):
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        if not hasattr(app, "extensions"):
            app.extensions = {}
        elif app.extensions.get(__name__, None):
            raise RuntimeError("Sentry registration is already registered")
        app.extensions[__name__] = True

        appcontext_pushed.connect(_push_appctx, sender=app)
        app.teardown_appcontext(_pop_appctx)
        got_request_exception.connect(_capture_exception, sender=app)
        app.before_request(_before_request)


def _push_appctx(*args, **kwargs):
    get_current_hub().push_scope()
    _app_ctx_stack.top._sentry_app_scope_pushed = True


def _pop_appctx(exception):
    get_current_hub().pop_scope_unsafe()


def _capture_exception(sender, exception, **kwargs):
    capture_exception(exception)


def _before_request(*args, **kwargs):
    try:
        assert getattr(
            _app_ctx_stack.top, "_sentry_app_scope_pushed", None
        ), "scope push failed"

        with configure_scope() as scope:
            if request.url_rule:
                scope.transaction = request.url_rule.endpoint

            try:
                scope.request = _get_request_info()
            except Exception:
                get_current_hub().capture_internal_exception()

            try:
                scope.user = _get_user_info()
            except Exception:
                get_current_hub().capture_internal_exception()
    except Exception:
        get_current_hub().capture_internal_exception()


def _get_request_info():
    return {
        "url": "%s://%s%s" % (request.scheme, request.host, request.path),
        "query_string": request.query_string,
        "method": request.method,
        "data": request.get_data(cache=True, as_text=True, parse_form_data=True),
        "headers": dict(request.headers),
        "env": get_environ(request.environ),
    }


def _get_user_info():
    try:
        ip_address = request.access_route[0]
    except IndexError:
        ip_address = request.remote_addr

    user_info = {"id": None, "ip_address": ip_address}

    try:
        user_info["id"] = current_user.get_id()
        # TODO: more configurable user attrs here
    except AttributeError:
        # might happen if:
        # - flask_login could not be imported
        # - flask_login is not configured
        # - no user is logged in
        pass

    return user_info