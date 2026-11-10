"""Print the findings in SARIF files (CodeQL output) and exit 1 if any is high severity (>= 7.0)."""
import glob
import json
import sys

HIGH = 7.0


def main(folder):
    high = total = 0
    for path in sorted(glob.glob(f"{folder}/*.sarif")):
        with open(path) as fh:
            sarif = json.load(fh)
        for run in sarif.get("runs", []):
            rules = {}
            for comp in [run["tool"]["driver"], *run["tool"].get("extensions", [])]:
                for rule in comp.get("rules", []):
                    rules[rule["id"]] = rule
            for res in run.get("results", []):
                rule = rules.get(res.get("ruleId"), {})
                sev = float(rule.get("properties", {}).get("security-severity", 0) or 0)
                loc = res.get("locations", [{}])[0].get("physicalLocation", {})
                where = f"{loc.get('artifactLocation', {}).get('uri', '?')}:{loc.get('region', {}).get('startLine', '?')}"
                msg = res.get("message", {}).get("text", "").splitlines()[0][:160]
                print(f"{'HIGH' if sev >= HIGH else 'note'} {sev:4.1f} {res.get('ruleId')} {where} {msg}")
                total += 1
                high += sev >= HIGH
    print(f"{total} finding(s), {high} high severity")
    return 1 if high else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
