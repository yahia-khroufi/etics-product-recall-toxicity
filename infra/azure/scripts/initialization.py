"""Run/check an explicit initialization job. Never log secrets or raw CLI errors."""
import argparse
import json
import subprocess
import time


def field(item, name, default=None):
    return item.get(name, item.get("properties", {}).get(name, default))


def azure(arguments):
    result = subprocess.run(["az", *arguments, "--output", "json", "--only-show-errors"],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError("La commande Azure du job a échoué ; consulter le portail Azure.")
    return json.loads(result.stdout)


def latest_success(executions):
    if not executions:
        raise RuntimeError("Exécuter d'abord le job d'initialisation.")
    latest = max(executions, key=lambda e: str(field(e, "startTime", "")))
    if field(latest, "status") != "Succeeded":
        raise RuntimeError("La dernière initialisation n'a pas réussi.")
    return latest


def manage(mode, resource_group, job_name, timeout=960):
    if not job_name:
        raise RuntimeError("Créer d'abord le job avec une image publiée.")
    base = ["containerapp", "job"]
    common = ["--resource-group", resource_group, "--name", job_name]
    executions = azure(base+["execution", "list"]+common)
    if mode == "verify":
        latest_success(executions)
        print("Dernière initialisation vérifiée : Succeeded.")
        return
    if any(field(e, "status") in {"Running", "Processing", "Pending"} for e in executions):
        raise RuntimeError("Un job d'initialisation est déjà actif.")
    started = azure(base+["start"]+common)
    execution_name = started.get("name")
    if not execution_name:
        raise RuntimeError("Azure n'a pas confirmé le nom de l'exécution.")
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        execution = azure(base+["execution", "show"]+common+["--job-execution-name", execution_name])
        status = field(execution, "status")
        if status == "Succeeded":
            print("Initialisation terminée : Succeeded.")
            return
        if status in {"Failed", "Stopped", "Degraded"}:
            raise RuntimeError("Le job d'initialisation a échoué ; les applications ne doivent pas être activées.")
        time.sleep(10)
    raise RuntimeError("Délai d'initialisation dépassé ; vérifier Azure avant toute nouvelle tentative.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["start", "verify"])
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--job-name", required=True)
    args = parser.parse_args()
    try:
        manage(args.mode, args.resource_group, args.job_name)
    except (RuntimeError, OSError, ValueError) as error:
        # CLI provider response bodies and subprocess arguments are not printed.
        print(str(error) if isinstance(error, RuntimeError) else "Configuration CLI Azure indisponible.")
        raise SystemExit(1) from None
