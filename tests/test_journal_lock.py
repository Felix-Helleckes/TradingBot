import importlib.util, os, tempfile, json

# load utils
utils_path = os.path.join(os.path.dirname(__file__), '..', 'utils.py')
spec = importlib.util.spec_from_file_location('utils', os.path.abspath(utils_path))
utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utils)


def test_atomic_write_and_append():
    d = tempfile.mkdtemp(prefix='tbtest_')
    try:
        jpath = os.path.join(d, 'logs', 'test_events.jsonl')
        os.makedirs(os.path.dirname(jpath), exist_ok=True)
        obj = {'a': 1, 'b': 'x'}
        ok = utils.atomic_write_json(os.path.join(d, 'state.json'), {'k': 'v'})
        assert ok
        with open(os.path.join(d, 'state.json'), 'r', encoding='utf-8') as f:
            s = json.load(f)
        assert s == {'k': 'v'}

        # append 3 JSONL lines
        for i in range(3):
            ok = utils.append_jsonl_locked(jpath, {'i': i})
            assert ok
        with open(jpath, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
        assert len(lines) == 3
        for idx, line in enumerate(lines):
            assert json.loads(line)['i'] == idx
        print('OK')
    finally:
        try:
            import shutil
            shutil.rmtree(d)
        except Exception:
            pass


if __name__ == '__main__':
    test_atomic_write_and_append()
