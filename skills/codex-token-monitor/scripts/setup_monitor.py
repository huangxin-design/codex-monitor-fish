"""Create a project-scoped monitor from the bundled template (stdlib only)."""
import argparse
import json
import os
from pathlib import Path
import shutil

SKILL = Path(__file__).resolve().parents[1]


def setup(project, output, codex_home=None, name=None, port=18766, avatar=None, exclude=()):
    project = Path(project).expanduser().absolute()
    output = Path(output).expanduser().resolve()
    home = Path(codex_home or os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser().resolve()
    if not project.is_dir():
        raise ValueError('项目目录不存在，请选择实际的 Codex 项目目录。')
    if not home.is_dir():
        raise ValueError('未找到 Codex 数据目录。先在本机运行 Codex，或指定 --codex-home。')
    if output == home or home in output.parents or output == SKILL or SKILL in output.parents:
        raise ValueError('输出目录不能位于 Codex 数据目录或 Skill 包内。')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('输出目录非空，已保留现有内容。请直接使用现有监控器或选择新目录。')
    if not 1024 <= port <= 65535:
        raise ValueError('端口必须在 1024 到 65535 之间。')
    avatar_path = Path(avatar).expanduser().resolve() if avatar else None
    if avatar_path and (not avatar_path.is_file() or avatar_path.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp')):
        raise ValueError('头像需要是本地 JPG、PNG 或 WebP 图片。')
    template = SKILL / 'assets' / 'app'
    required = ('monitor.py', 'launch.py', 'index.html', 'assets/avatar.svg', 'assets/logo.jpg')
    if not all((template / item).is_file() for item in required):
        raise ValueError('Skill 资源不完整，请重新解压整个 Skill 文件夹。')
    shutil.copytree(template, output, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'test_*.py', '*.log'))
    config = {
        'codex_home': str(home), 'project_directory': str(project),
        'project_name': name or project.name, 'port': port, 'refresh_seconds': 10,
        'tasks': [], 'exclude_task_ids': list(exclude),
    }
    if avatar_path:
        relative = 'assets/avatar' + avatar_path.suffix.lower()
        shutil.copyfile(avatar_path, output / relative)
        config['avatar_file'] = relative
    (output / 'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'output': str(output), 'project': str(project), 'url': f'http://127.0.0.1:{port}/',
            'next': 'Run launch.py in the output folder with the same Python interpreter.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, help='Exact project working directory')
    parser.add_argument('--output', required=True, help='New or empty destination directory')
    parser.add_argument('--codex-home', help='Defaults to CODEX_HOME, then ~/.codex')
    parser.add_argument('--name', help='Project display name; defaults to directory name')
    parser.add_argument('--port', type=int, default=18766)
    parser.add_argument('--avatar', help='Optional local JPG/PNG/WebP for the right-side user avatar')
    parser.add_argument('--exclude-task', action='append', default=[], help='Exclude a task and its descendants; repeatable')
    args = parser.parse_args()
    try:
        result = setup(args.project, args.output, args.codex_home, args.name, args.port, args.avatar, args.exclude_task)
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
