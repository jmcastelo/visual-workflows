import pytest

from visual_workflows.core.transport import CommandTimeout, LocalTransport, TransportError


async def test_commands_run_in_the_given_home(tmp_path):
    async with LocalTransport(tmp_path) as t:
        r = await t.run("echo $HOME; pwd")
        assert r.stdout.split() == [str(tmp_path)] * 2
        with pytest.raises(CommandTimeout):
            await t.run("sleep 5", timeout=0.2)


async def test_files(tmp_path):
    (tmp_path / "home").mkdir()
    async with LocalTransport(tmp_path / "home") as t:
        await t.write_text("a/b/job.sh", "#!/bin/bash\n")
        assert (await t.stat("a/b/job.sh")).size == 12
        assert [f.name for f in await t.listdir("a")] == ["b"]
        await t.get("a/b/job.sh", tmp_path / "out" / "job.sh")
        await t.put(tmp_path / "out" / "job.sh", "c/job.sh")
        assert await t.read_text("c/job.sh") == "#!/bin/bash\n"
        assert await t.stat("missing") is None
        with pytest.raises(TransportError):
            await t.listdir("missing")


async def test_requires_connection(tmp_path):
    with pytest.raises(TransportError, match="Not connected"):
        await LocalTransport(tmp_path).run("true")
