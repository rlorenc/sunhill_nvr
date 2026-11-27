"""Render service configuration from an already verified release."""
from __future__ import annotations
import argparse
from pathlib import Path
from .config import load
from .deployment import generate


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(prog='sunhill-render-deployment')
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--lan-tls',action='store_true')
    parser.add_argument('--rtsp-only',action='store_true')
    args=parser.parse_args(argv)
    tls={'tls_certificate':Path('/opt/sunhill-nvr/config/tls/current/fullchain.pem'),'tls_key':Path('/opt/sunhill-nvr/config/tls/current/key.pem')} if args.lan_tls else {}
    generate(load(args.config),args.output,rtsp_only=args.rtsp_only,**tls)
    return 0


if __name__=='__main__':raise SystemExit(main())
