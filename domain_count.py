import argparse
import csv
import multiprocessing as mp
import os
import sys
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

# Windows의 ProcessPoolExecutor는 max_workers > 61에서 ValueError를 낸다.
WINDOWS_MAX_WORKERS = 61


def count_domains(emails):
    """배치 내 이메일들의 도메인별 개수를 센다(워커 프로세스에서 실행)."""
    counts = Counter()
    for email in emails:
        email = (email or "").strip()
        local, separator, domain = email.rpartition("@")
        if not (separator and local and domain) or any(c.isspace() for c in email):
            continue
        # 로컬 파트의 '@'는 따옴표 로컬 파트("a@b"@example.com)일 때만 허용
        if "@" in local and not (len(local) >= 2 and local[0] == local[-1] == '"'):
            continue
        counts[domain.casefold()] += 1
    return counts


def find_column(fieldnames, wanted):
    """헤더에서 열 이름을 앞뒤 공백·대소문자 무시하고 찾아 실제 이름을 돌려준다."""
    key = wanted.strip().casefold()
    for name in fieldnames or []:
        if name is not None and name.strip().casefold() == key:
            return name
    raise ValueError(f"이메일 열을 찾을 수 없습니다: {wanted}")


def read_batches(reader, email_column, batch_size):
    """DictReader에서 이메일 열만 뽑아 batch_size 단위로 흘려보낸다."""
    batch = []
    for row in reader:
        batch.append(row.get(email_column))
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def drain(pending, totals, return_when):
    """완료된 future를 totals에 합치고 남은 집합을 돌려준다(중복 로직 통합)."""
    done, pending = wait(pending, return_when=return_when)
    for future in done:
        totals.update(future.result())
    return pending


def resolve_workers(requested):
    """플랫폼 제한에 맞춰 실제 워커 수를 정한다(Windows는 61 이하)."""
    if sys.platform == "win32":
        return min(requested, WINDOWS_MAX_WORKERS)
    return requested


def aggregate(input_path, email_column, encoding, batch_size, workers):
    totals = Counter()
    with open(input_path, "r", encoding=encoding, newline="") as source:
        reader = csv.DictReader(source)
        email_column = find_column(reader.fieldnames, email_column)

        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=mp.get_context("spawn"),
        ) as executor:
            pending = set()
            for batch in read_batches(reader, email_column, batch_size):
                pending.add(executor.submit(count_domains, batch))
                # 미완료 작업 수를 제한해 입력 크기와 무관하게 메모리를 묶어 둔다.
                if len(pending) >= workers * 2:
                    pending = drain(pending, totals, FIRST_COMPLETED)

            while pending:
                pending = drain(pending, totals, FIRST_COMPLETED)
    return totals


def write_results(output_path, totals):
    with open(output_path, "w", encoding="utf-8-sig", newline="") as target:
        writer = csv.writer(target)
        writer.writerow(["domain", "member_count"])
        # 회원 수 내림차순, 동률이면 도메인 오름차순으로 결정적 정렬.
        writer.writerows(sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])))


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("양의 정수를 입력하세요.")
    return number


def same_file(input_path, output_path):
    if os.path.normcase(input_path) == os.path.normcase(output_path):
        return True
    try:
        return os.path.samefile(input_path, output_path)
    except OSError:  # 출력 파일이 아직 없으면 같은 파일일 수 없다.
        return False


def main():
    parser = argparse.ArgumentParser(description="CSV 회원 데이터의 이메일 도메인별 집계")
    parser.add_argument("input", help="입력 CSV 파일")
    parser.add_argument("output", help="출력 CSV 파일")
    parser.add_argument("--email-column", default="email")
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--batch-size", type=positive_int, default=50_000)
    parser.add_argument("--workers", type=positive_int, default=os.cpu_count() or 1)
    args = parser.parse_args()

    input_path = os.path.realpath(args.input)
    output_path = os.path.realpath(args.output)
    if same_file(input_path, output_path):
        parser.error("입력 파일과 출력 파일은 달라야 합니다.")

    workers = resolve_workers(args.workers)
    try:
        totals = aggregate(
            input_path,
            args.email_column,
            args.encoding,
            args.batch_size,
            workers,
        )
        write_results(output_path, totals)
    except (OSError, ValueError, UnicodeDecodeError, csv.Error) as error:
        sys.exit(f"오류: {error}")


if __name__ == "__main__":
    mp.freeze_support()
    main()