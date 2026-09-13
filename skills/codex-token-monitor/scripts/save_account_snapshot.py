"""Save a minimal official account snapshot supplied by the Codex host on stdin."""
import argparse
import json
from pathlib import Path
import sys

SKILL = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(SKILL / 'assets/app'))
from account_usage import save_account_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--codex-home', type=Path)
    args = parser.parse_args()
    directory = args.data_dir.expanduser().resolve()
    if directory == SKILL or SKILL in directory.parents:
        parser.error('账户快照应保存在启动器返回的 data_dir 中，不能写进 Skill 包。')
    try:
        raw = sys.stdin.read(262145)
        if len(raw) > 262144:
            raise ValueError('账户响应过大。')
        save_account_snapshot(directory, json.loads(raw), codex_home=args.codex_home)
    except (OSError, ValueError, TypeError):
        parser.exit(1, '账户快照无效或无法保存，未输出账户原始内容。\n')
    print(json.dumps({'ok': True, 'path': str(directory / 'account-usage.json')}))


if __name__ == '__main__':
    main()
