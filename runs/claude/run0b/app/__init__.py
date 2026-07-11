"""Application factory for the invoice approval service."""
import os

from flask import Flask, jsonify

from . import db


def create_app(test_config=None):
    app = Flask(__name__)

    base_dir = os.path.abspath(os.path.join(app.root_path, ".."))
    data_dir = os.path.join(base_dir, "data")

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        DATABASE=os.environ.get("DATABASE", os.path.join(data_dir, "app.db")),
        UPLOAD_DIR=os.environ.get("UPLOAD_DIR", os.path.join(data_dir, "uploads")),
        EMAIL_LOG=os.environ.get("EMAIL_LOG", os.path.join(data_dir, "emails.log")),
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    os.makedirs(os.path.dirname(os.path.abspath(app.config["DATABASE"])), exist_ok=True)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    db.init_app(app)

    from . import auth, api, views, commands, service

    app.register_blueprint(auth.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(views.bp)
    commands.init_app(app)

    @app.errorhandler(service.ServiceError)
    def handle_service_error(err):
        return jsonify({"error": str(err)}), err.status

    @app.errorhandler(413)
    def handle_too_large(err):
        return jsonify({"error": "attachment too large"}), 413

    with app.app_context():
        db.init_db()

    return app
