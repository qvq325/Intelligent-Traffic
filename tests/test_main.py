import main as entrypoint


def test_main_loads_server_address_from_project_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VIDEOTEST_HOST=0.0.0.0\nVIDEOTEST_PORT=8123\n",
        encoding="utf-8",
    )
    run_arguments = {}

    monkeypatch.delenv("VIDEOTEST_HOST", raising=False)
    monkeypatch.delenv("VIDEOTEST_PORT", raising=False)
    monkeypatch.setattr(entrypoint, "ENV_FILE", env_file)
    monkeypatch.setattr(
        entrypoint.uvicorn,
        "run",
        lambda application, **kwargs: run_arguments.update(kwargs),
    )

    entrypoint.main()

    assert run_arguments == {
        "host": "0.0.0.0",
        "port": 8123,
        "log_level": "info",
    }


def test_system_environment_overrides_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("VIDEOTEST_PORT=8123\n", encoding="utf-8")
    run_arguments = {}

    monkeypatch.setenv("VIDEOTEST_PORT", "9000")
    monkeypatch.setattr(entrypoint, "ENV_FILE", env_file)
    monkeypatch.setattr(
        entrypoint.uvicorn,
        "run",
        lambda application, **kwargs: run_arguments.update(kwargs),
    )

    entrypoint.main()

    assert run_arguments["port"] == 9000
