"""Download the pinned SudachiDict / Mozc OSS inputs into input/ and verify their SHA-256."""
import hashlib
import os
import sys
import urllib.request
import zipfile

SUDACHI = "https://sudachi.s3-ap-northeast-1.amazonaws.com/sudachidict-raw/v1/20260723/"
MOZC = "https://raw.githubusercontent.com/google/mozc/ff395cea46fe1438948e2fa876be19600e61ac00/src/data/dictionary_oss/"
INPUTS = [
    (SUDACHI + "small_lex.zip", "small_lex.zip", "e49936daef64043657752eb9f4ada912cf0316e26dd158a6200c770e2f93e706"),
    (SUDACHI + "core_lex.zip", "core_lex.zip", "d8ed376d8ff368226314a43151ab02591378dd344bff017d8a93ae9839212edf"),
] + [
    (MOZC + f"dictionary0{i}.txt", f"mozc/dictionary0{i}.txt", sha) for i, sha in enumerate((
        "9e07ce292932c4fff327ed8cb06274c09acca556bd8bdf7c7ef1f6173eae3b61",
        "836fd5fd8399510de0d8ef7b33be913cca6bbaee7b712cc8045fba03f4c75a89",
        "93943858d0cfd092b849a78def6901eaf228177e1ac5663544e865a3a1e45a9e",
        "cb9f1b0b4a7550d682d86244a021cd17e0b15366328819e69135b5f183a1a24b",
        "9fdc4e3948d03463de13ab5782adffbc11dd06e934cb80138c5920029cd3d2f6",
        "199e3b0f050b691823ae7ba943090d437f51af6436b27c5f3dc69cafa12000e4",
        "b95b14f7770ae2a824bc8e9fad8b03c1bc45c504f93a36554b8b56daa0368d2a",
        "7f53649b529d4c8c495d3a8dd9c4d489dfe7696080f7423107ff01331174be48",
        "50a31bb5fc1646721b00be371a5695dffab1292bda143e1e59cc70cbbdd8643f",
        "4e5fcd4cbfbf11395166a37b506f547fc54d20edc0884f5184ecd3fc705f0d9a",
    ))
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main(dest="input"):
    for url, name, expected in INPUTS:
        path = os.path.join(dest, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not (os.path.exists(path) and sha256(path) == expected):
            print(f"fetch {url}")
            urllib.request.urlretrieve(url, path)
        if sha256(path) != expected:
            sys.exit(f"sha256 mismatch: {path}")
        if name.endswith(".zip"):
            member = name[:-4] + ".csv"
            with zipfile.ZipFile(path) as zf, open(os.path.join(dest, member), "wb") as out:
                out.write(zf.read(member))  # only the known member; never extract archive paths
    print("inputs ok")


if __name__ == "__main__":
    main(*sys.argv[1:])
