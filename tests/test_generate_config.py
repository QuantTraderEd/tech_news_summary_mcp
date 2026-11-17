import os
import json
import shlex
import shutil
import stat
import subprocess
import sys

import pytest

# scripts/generate_config.sh 의 회귀 테스트.
# 실행: pytest -vs tests/test_generate_config.py
#
# 주의: 스크립트는 "현재 디렉터리"에 config.json 을 쓴다. 저장소 루트에는 실제 비밀 값이 든
# config.json 이 있으므로, 항상 pytest 의 tmp_path 를 cwd 로 해서 실행하고
# 저장소 루트를 cwd 로 하는 실행은 헬퍼에서 막는다. 자식 프로세스에는 os.environ 을 물려주지 않는다.

test_dir = os.path.dirname(os.path.abspath(__file__))
pjt_home_path = os.path.realpath(os.path.join(test_dir, os.pardir))
SCRIPT_PATH = os.path.join(pjt_home_path, "scripts", "generate_config.sh")
BASH = shutil.which("bash")

KEYS = [
    "GEMINI_API_KEY",
    "TWEET_USERNAME",
    "TWEET_PASSWORD",
    "VERIFICATION_INFO",
    "RECEIVER_EMAIL_LIST",
]
STRING_KEYS = KEYS[:4]

DEFAULTS = {
    "GEMINI_API_KEY": "k",
    "TWEET_USERNAME": "u",
    "TWEET_PASSWORD": "p",
    "VERIFICATION_INFO": "v",
    "RECEIVER_EMAIL_LIST": "a@example.com,b@example.com",
}

DEFAULT_PATH = os.pathsep.join([os.path.dirname(sys.executable), "/usr/bin", "/bin"])


def _run(cwd, args, env_values=None, path=DEFAULT_PATH):
    """스크립트를 tmp 디렉터리에서 실행한다. env 는 PATH 와 지정한 값만 넘긴다."""
    cwd = str(cwd)
    if os.path.realpath(cwd) == pjt_home_path:
        raise AssertionError("저장소 루트에서 generate_config.sh 를 실행하면 실제 config.json 을 덮어쓴다")
    assert os.path.isfile(SCRIPT_PATH), SCRIPT_PATH
    env = {"PATH": path}
    if env_values:
        env.update(env_values)
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def run_script(cwd, env_values, path=DEFAULT_PATH):
    """bash <script> 로 직접 실행."""
    return _run(cwd, [BASH, SCRIPT_PATH], env_values, path)


def run_sourced(cwd, env_values, path=DEFAULT_PATH):
    """gunicorn_start.sh 처럼 source 로 실행."""
    return _run(cwd, [BASH, "-c", '. "$1"', "_", SCRIPT_PATH], env_values, path)


def make_env(**overrides):
    values = dict(DEFAULTS)
    values.update(overrides)
    return values


def read_config(cwd):
    with open(os.path.join(str(cwd), "config.json"), encoding="utf-8") as f:
        return json.load(f)


ROUNDTRIP_VALUES = [
    pytest.param('a"b', id="double-quote"),
    pytest.param("a\\b", id="backslash"),
    pytest.param('p"a\\bc', id="quote-and-backslash-b"),
    pytest.param("a\\bc", id="backslash-b"),
    pytest.param("a\\nb", id="backslash-n-literal"),
    pytest.param("a\nb", id="real-newline"),
    pytest.param("a\tb", id="tab"),
    pytest.param("$HOME", id="dollar-var"),
    pytest.param("$(id)", id="command-substitution"),
    pytest.param("`id`", id="backtick"),
    pytest.param("it's", id="single-quote"),
    pytest.param("  a b  c  ", id="spaces"),
    pytest.param("비밀번호한글", id="korean"),
    pytest.param("%s%d", id="printf-format"),
    pytest.param("-n", id="echo-option-n"),
    pytest.param("-e", id="echo-option-e"),
]


@pytest.mark.parametrize("field", STRING_KEYS)
@pytest.mark.parametrize("value", ROUNDTRIP_VALUES)
def test_string_field_roundtrip(tmp_path, field, value):
    result = run_script(tmp_path, make_env(**{field: value}))
    assert result.returncode == 0, result.stdout + result.stderr
    config = read_config(tmp_path)
    assert config[field] == value
    for other in STRING_KEYS:
        if other != field:
            assert config[other] == DEFAULTS[other]


def test_all_fields_special_at_once(tmp_path):
    values = {
        "GEMINI_API_KEY": 'k"1\\n',
        "TWEET_USERNAME": "u\\bser",
        "TWEET_PASSWORD": 'p"a\\bc$(id)`id`',
        "VERIFICATION_INFO": "v\n\tv %s",
        "RECEIVER_EMAIL_LIST": DEFAULTS["RECEIVER_EMAIL_LIST"],
    }
    result = run_script(tmp_path, values)
    assert result.returncode == 0, result.stdout + result.stderr
    config = read_config(tmp_path)
    for key in STRING_KEYS:
        assert config[key] == values[key]


def test_keys_and_order(tmp_path):
    result = run_script(tmp_path, make_env())
    assert result.returncode == 0, result.stdout + result.stderr
    with open(tmp_path / "config.json", encoding="utf-8") as f:
        config = json.load(f, object_pairs_hook=lambda pairs: pairs)
    assert [k for k, _ in config] == KEYS


def test_plain_output_exact_bytes(tmp_path):
    result = run_script(tmp_path, make_env())
    assert result.returncode == 0, result.stdout + result.stderr
    expected = (
        '{\n'
        '  "GEMINI_API_KEY": "k",\n'
        '  "TWEET_USERNAME": "u",\n'
        '  "TWEET_PASSWORD": "p",\n'
        '  "VERIFICATION_INFO": "v",\n'
        '  "RECEIVER_EMAIL_LIST": ["a@example.com","b@example.com"]\n'
        '}\n'
    ).encode("utf-8")
    assert (tmp_path / "config.json").read_bytes() == expected
    assert "config.json" in result.stdout


@pytest.mark.parametrize(
    "email_list, expected",
    [
        pytest.param("a@example.com,b@example.com,c@example.com",
                     ["a@example.com", "b@example.com", "c@example.com"], id="multiple"),
        pytest.param("a@example.com", ["a@example.com"], id="single"),
        pytest.param('a"b@example.com,c@example.com', ['a"b@example.com', "c@example.com"], id="quote"),
        pytest.param("a\\b@example.com,c\\nd@example.com",
                     ["a\\b@example.com", "c\\nd@example.com"], id="backslash"),
        pytest.param("a@example.com, b@example.com",
                     ["a@example.com", " b@example.com"], id="keeps-leading-space"),
        pytest.param("a@example.com,,b@example.com",
                     ["a@example.com", "", "b@example.com"], id="keeps-empty-item"),
    ],
)
def test_email_list(tmp_path, email_list, expected):
    result = run_script(tmp_path, make_env(RECEIVER_EMAIL_LIST=email_list))
    assert result.returncode == 0, result.stdout + result.stderr
    assert read_config(tmp_path)["RECEIVER_EMAIL_LIST"] == expected


def test_sourced_execution(tmp_path):
    values = make_env(TWEET_PASSWORD='p"a\\bc', RECEIVER_EMAIL_LIST='a"@example.com,b@example.com')
    result = run_sourced(tmp_path, values)
    assert result.returncode == 0, result.stdout + result.stderr
    config = read_config(tmp_path)
    assert config["TWEET_PASSWORD"] == 'p"a\\bc'
    assert config["RECEIVER_EMAIL_LIST"] == ['a"@example.com', "b@example.com"]


def test_unexported_shell_variables(tmp_path):
    values = make_env(TWEET_PASSWORD='p"a\\bc $x', VERIFICATION_INFO="한글\ttab")
    assignments = "; ".join("%s=%s" % (k, shlex.quote(v)) for k, v in values.items())
    # env 에는 PATH 만 넣는다. 값은 export 되지 않은 셸 변수로만 존재한다.
    result = _run(tmp_path, [BASH, "-c", assignments + '; . "$1"', "_", SCRIPT_PATH])
    assert result.returncode == 0, result.stdout + result.stderr
    config = read_config(tmp_path)
    for key in STRING_KEYS:
        assert config[key] == values[key]
    assert config["RECEIVER_EMAIL_LIST"] == ["a@example.com", "b@example.com"]


@pytest.mark.parametrize("runner", [run_script, run_sourced], ids=["direct", "sourced"])
@pytest.mark.parametrize("missing", KEYS)
@pytest.mark.parametrize("mode", ["unset", "empty"])
def test_missing_env_var(tmp_path, runner, missing, mode):
    values = make_env()
    if mode == "unset":
        del values[missing]
    else:
        values[missing] = ""
    result = runner(tmp_path, values)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ERROR: %s 환경 변수가 설정되지 않았습니다." % missing in result.stdout
    assert not (tmp_path / "config.json").exists()


def test_missing_env_var_keeps_existing_config(tmp_path):
    (tmp_path / "config.json").write_text("OLD", encoding="utf-8")
    values = make_env()
    del values["TWEET_USERNAME"]
    result = run_script(tmp_path, values)
    assert result.returncode == 1
    assert (tmp_path / "config.json").read_text(encoding="utf-8") == "OLD"


SECRETS = {
    "GEMINI_API_KEY": "SECRET_KEY_aaa111",
    "TWEET_USERNAME": "SECRET_USER_bbb222",
    "TWEET_PASSWORD": "SECRET_PASS_ccc333",
    "VERIFICATION_INFO": "SECRET_VERI_ddd444",
    "RECEIVER_EMAIL_LIST": "secret_mail_eee555@example.com",
}


def _assert_no_secret_leak(result):
    output = result.stdout + result.stderr
    for value in SECRETS.values():
        assert value not in output


@pytest.mark.parametrize("runner", [run_script, run_sourced], ids=["direct", "sourced"])
def test_conversion_failure_keeps_existing_config(tmp_path, runner):
    """python3 를 찾을 수 없으면 실패하고, 기존 config.json 은 그대로여야 한다."""
    empty_bin = tmp_path / "empty_bin"
    empty_bin.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "config.json").write_text("OLD-CONTENT", encoding="utf-8")

    result = runner(workdir, dict(SECRETS), path=str(empty_bin))

    assert result.returncode != 0
    assert "ERROR:" in result.stdout
    assert "GEMINI_API_KEY" in result.stdout
    assert (workdir / "config.json").read_text(encoding="utf-8") == "OLD-CONTENT"
    _assert_no_secret_leak(result)


def test_conversion_failure_does_not_create_config(tmp_path):
    empty_bin = tmp_path / "empty_bin"
    empty_bin.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()

    result = run_script(workdir, dict(SECRETS), path=str(empty_bin))

    assert result.returncode != 0
    assert "ERROR:" in result.stdout
    assert not (workdir / "config.json").exists()
    _assert_no_secret_leak(result)


@pytest.mark.parametrize("failing_value", ["GEMINI_API_KEY", "TWEET_USERNAME", "TWEET_PASSWORD",
                                           "VERIFICATION_INFO", "RECEIVER_EMAIL_LIST"])
def test_conversion_failure_names_variable_on_nth_call(tmp_path, failing_value):
    """n 번째 python3 호출만 실패하게 만들어, 어느 변수에서 실패해도 파일을 건드리지 않고 이름을 알린다."""
    fail_at = KEYS.index(failing_value) + 1
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counter = tmp_path / "counter"
    wrapper = bin_dir / "python3"
    # 호출은 서브셸에서 일어나므로 카운터는 파일로 유지한다.
    wrapper.write_text(
        "#!/bin/sh\n"
        'n=$(cat %(c)s 2>/dev/null || echo 0)\n'
        'n=$((n + 1))\n'
        'echo $n > %(c)s\n'
        'if [ "$n" -eq %(fail)d ]; then exit 3; fi\n'
        'exec %(py)s "$@"\n'
        % {"c": shlex.quote(str(counter)), "fail": fail_at, "py": shlex.quote(sys.executable)},
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "config.json").write_text("OLD-CONTENT", encoding="utf-8")

    result = run_script(workdir, dict(SECRETS), path=os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ERROR: %s 값을 JSON 으로 변환하지 못했습니다." % failing_value in result.stdout
    assert (workdir / "config.json").read_text(encoding="utf-8") == "OLD-CONTENT"
    _assert_no_secret_leak(result)


def test_secret_values_not_passed_as_process_arguments(tmp_path):
    """python3 래퍼가 받은 인자를 기록해, 비밀 값이 프로세스 인자로 새지 않는지 확인한다."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "args.log"
    wrapper = bin_dir / "python3"
    wrapper.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do printf \'%%s\\n\' "$a" >> %s; done\n'
        'printf \'%%s\\n\' "--call--" >> %s\n'
        'exec %s "$@"\n' % (shlex.quote(str(log)), shlex.quote(str(log)), shlex.quote(sys.executable)),
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    workdir = tmp_path / "work"
    workdir.mkdir()

    result = run_script(workdir, dict(SECRETS), path=os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]))

    assert result.returncode == 0, result.stdout + result.stderr
    # 래퍼가 실제로 호출되었는지(= 검증이 의미 있는지) 확인한다.
    logged = log.read_text(encoding="utf-8")
    assert logged.count("--call--") == 5  # 문자열 4개 + 이메일 1개
    for value in SECRETS.values():
        assert value not in logged
    config = read_config(workdir)
    assert config["TWEET_PASSWORD"] == SECRETS["TWEET_PASSWORD"]
    assert config["RECEIVER_EMAIL_LIST"] == [SECRETS["RECEIVER_EMAIL_LIST"]]


def test_guard_refuses_repo_root():
    """헬퍼가 저장소 루트에서의 실행을 거부하는지 확인한다(스크립트는 실행되지 않는다)."""
    with pytest.raises(AssertionError):
        run_script(pjt_home_path, make_env())
