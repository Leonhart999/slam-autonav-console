"""Read the index of a ROS1 bag and print it as JSON (runs in the plugin's venv with rosbags).

Usage: python bagtool.py inspect <file.bag>
Output: {duration, start, end, messages, size, topics: [{topic, type, count, hz, livox, image}]}
"""
import json
import os
import sys
from pathlib import Path

from livox import LIVOX_TYPES

IMAGE_TYPES = ('sensor_msgs/msg/Image', 'sensor_msgs/msg/CompressedImage')


def inspect(path):
    from rosbags.rosbag1 import Reader
    with Reader(Path(path)) as reader:
        duration = max(reader.duration, 0) / 1e9
        topics = {}
        for c in reader.connections:
            t = topics.setdefault(c.topic, {'topic': c.topic, 'type': c.msgtype, 'count': 0})
            t['count'] += c.msgcount
            if t['type'] != c.msgtype:
                t['type'] = f"{t['type']} | {c.msgtype}"
        out = []
        for t in sorted(topics.values(), key=lambda x: x['topic']):
            out.append({**t, 'hz': round(t['count'] / duration, 2) if duration > 0 else None,
                        'livox': t['type'] in LIVOX_TYPES, 'image': t['type'] in IMAGE_TYPES})
        return {'duration': round(duration, 3), 'start': reader.start_time, 'end': reader.end_time,
                'messages': reader.message_count, 'size': os.path.getsize(path), 'topics': out}


def main():
    if len(sys.argv) != 3 or sys.argv[1] != 'inspect':
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    try:
        print(json.dumps(inspect(sys.argv[2])))
    except Exception as e:  # noqa: BLE001  reported to the page
        print(json.dumps({'error': f'{type(e).__name__}: {e}'}))
        sys.exit(1)


if __name__ == '__main__':
    main()
