"""WSGI entrypoint. Used by `flask --app wsgi run` and production servers."""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
