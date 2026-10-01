from prometheus_client import Gauge, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST
from http.server import BaseHTTPRequestHandler, HTTPServer
import re
import os

registry = CollectorRegistry()
metric_defs = {}

log_paths = {
    "mac": "/oai-gnb-logs/nrMAC_stats.log",
    "l1": "/oai-gnb-logs/nrL1_stats.log",
    "rrc": "/oai-gnb-logs/nrRRC_stats.log",
}

def get_or_create_metric(metric_name, description, labelnames):
    if metric_name not in metric_defs:
        metric_defs[metric_name] = Gauge(metric_name, description, labelnames=labelnames, registry=registry)
    return metric_defs[metric_name]

def parse_logs():
    global registry, metric_defs
    # Reset registry and metrics to avoid exporting stale values
    registry = CollectorRegistry()
    metric_defs = {}

    # L1 stats
    try:
        if os.path.isfile(log_paths["l1"]):
            prb_noise_values = []
            collecting_matrix = False
            current_ul_rnti = None  # per-antenna ULSCH lines (4-RX RUs) follow their header line

            with open(log_paths["l1"], "r") as f:
                for line in f:
                    if match := re.search(r"Blacklisted PRBs (\d+)/(\d+)", line):
                        collecting_matrix = True
                        blacklisted = int(match.group(1))
                        total = int(match.group(2))
                        get_or_create_metric("oai_gnb_l1_total_prbs", "Total number of PRBs", []).set(total)
                        get_or_create_metric("oai_gnb_l1_blacklisted_prbs_total", "Number of blacklisted PRBs", []).set(blacklisted)
                        get_or_create_metric("oai_gnb_l1_blacklisted_prbs_ratio", "Ratio of blacklisted PRBs", []).set(blacklisted / total if total > 0 else 0)
                    elif match := re.search(r"DLSCH RNTI (\w+): current_Qm (\d+), current_RI (\d+), total_bytes TX (\d+)", line):
                        get_or_create_metric("oai_gnb_l1_dlsch_current_qm", "Current DLSCH Modulation Order", ["rnti"]).labels(rnti=match.group(1)).set(int(match.group(2)))
                        get_or_create_metric("oai_gnb_l1_dlsch_current_ri", "Current DLSCH Rank Indicator", ["rnti"]).labels(rnti=match.group(1)).set(int(match.group(3)))
                        get_or_create_metric("oai_gnb_l1_dlsch_tx_bytes", "DLSCH TX Bytes", ["rnti"]).labels(rnti=match.group(1)).set(int(match.group(4)))
                    elif match := re.search(r"ULSCH RNTI\s+(\w+), \d+: .*?ulsch_power\[0\] ([\d,]+).*?ulsch_noise_power\[0\] ([\d.]+), sync_pos (-?\d+)", line):
                        rnti = match.group(1)
                        current_ul_rnti = rnti
                        power = float(match.group(2).replace(",", "."))
                        noise = float(match.group(3))
                        sync_pos = int(match.group(4))
                        get_or_create_metric("oai_gnb_l1_ulsch_power", "ULSCH Power", ["rnti"]).labels(rnti=rnti).set(power)
                        get_or_create_metric("oai_gnb_l1_ulsch_noise_power", "ULSCH Noise Power", ["rnti"]).labels(rnti=rnti).set(noise)
                        get_or_create_metric("oai_gnb_l1_ulsch_sync_pos", "ULSCH Sync Position", ["rnti"]).labels(rnti=rnti).set(sync_pos)
                        get_or_create_metric("oai_gnb_l1_ulsch_power_antenna", "ULSCH Power per RX antenna", ["rnti", "antenna"]).labels(rnti=rnti, antenna="0").set(power)
                        get_or_create_metric("oai_gnb_l1_ulsch_noise_power_antenna", "ULSCH Noise Power per RX antenna", ["rnti", "antenna"]).labels(rnti=rnti, antenna="0").set(noise)
                    elif current_ul_rnti and (match := re.search(r"^\s*ulsch_power\[(\d+)\] (-?\d+(?:\.\d+)?), ulsch_noise_power\[\d+\] (-?\d+(?:\.\d+)?)", line)):
                        # Continuation line for RX antenna >= 1, e.g. "ulsch_power[1] 54.8, ulsch_noise_power[1] 52.9"
                        get_or_create_metric("oai_gnb_l1_ulsch_power_antenna", "ULSCH Power per RX antenna", ["rnti", "antenna"]).labels(rnti=current_ul_rnti, antenna=match.group(1)).set(float(match.group(2)))
                        get_or_create_metric("oai_gnb_l1_ulsch_noise_power_antenna", "ULSCH Noise Power per RX antenna", ["rnti", "antenna"]).labels(rnti=current_ul_rnti, antenna=match.group(1)).set(float(match.group(3)))
                    elif match := re.search(r"max_IO = (-?\d+) \((\d+)\), min_I0 = (-?\d+) \((\d+)\), avg_I0 = (-?\d+)", line):
                        collecting_matrix = False
                        if ant := re.search(r"avg_I0 = -?\d+ dB\(([-\d.]+)\)", line):
                            for aa, v in enumerate(re.findall(r"-?\d+", ant.group(1))):
                                get_or_create_metric("oai_gnb_l1_i0_avg_antenna_db", "Average I0 per RX antenna (dB)", ["antenna"]).labels(antenna=str(aa)).set(int(v))
                        get_or_create_metric("oai_gnb_l1_i0_max_db", "Max subband I0 (dB)", []).set(int(match.group(1)))
                        get_or_create_metric("oai_gnb_l1_i0_min_db", "Min subband I0 (dB)", []).set(int(match.group(3)))
                        get_or_create_metric("oai_gnb_l1_i0_avg_db", "Average I0 across subbands (dB)", []).set(int(match.group(5)))
                    elif match := re.search(r"PRACH I0 = (\d+)\.(\d+) dB", line):
                        prach_i0 = float(f"{match.group(1)}.{match.group(2)}")
                        get_or_create_metric("oai_gnb_l1_prach_i0_db", "PRACH I0 value (dB)", []).set(prach_i0)
                    elif collecting_matrix:
                        nums = re.findall(r"-?\d+", line)
                        prb_noise_values.extend(map(int, nums))
            
            for i, val in enumerate(prb_noise_values):
                get_or_create_metric("oai_gnb_l1_i0_noise_offset_db", "PRB I0 noise deviation from average in dB", ["prb", "prb_index"]).labels(prb=str(i).zfill(3), prb_index=str(i)).set(val)

    except Exception as e:
        print(f"[ERROR] L1 parsing failed: {e}")

    # MAC stats
    try:
        if os.path.isfile(log_paths["mac"]):
            with open(log_paths["mac"], "r") as f:
                lines = f.readlines()
                current_rnti = None
                current_cu_ue_id = None
                for line in lines:
                    if match := re.search(r"CU-UE-ID \(none\)", line):
                        current_rnti = None
                        current_cu_ue_id = None
                        continue  # skip this UE completely

                    if match := re.search(r"UE RNTI (\w+) CU-UE-ID (\d+).*?PH (\d+) dB.*?PCMAX (\d+) dBm(?:.*?average RSRP (-?\d+))?", line):
                        current_rnti = match.group(1)
                        current_cu_ue_id = match.group(2)
                        get_or_create_metric("oai_gnb_mac_ph", "Power Headroom", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(3)))
                        get_or_create_metric("oai_gnb_mac_pcmax", "PCMAX", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(4)))
                        if match.group(5) is not None:
                            get_or_create_metric("oai_gnb_mac_avg_rsrp", "Average RSRP", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(5)))

                    if not current_rnti:
                        continue

                    if match := re.search(r"UE .*?CQI (\d+),? RI (\d+),? PMI\s*\(?([^,)\s\]]+(?:,[^,)\s\]]+)*)\)?", line):
                        get_or_create_metric("oai_gnb_mac_cqi", "CQI", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(1)))
                        get_or_create_metric("oai_gnb_mac_ri", "Rank Indicator", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(2)))

                        pmi_raw = match.group(3).replace(" ", "")  # normalize spacing
                        pmi_parts = pmi_raw.split(",")

                        for i, v in enumerate(pmi_parts):
                            get_or_create_metric("oai_gnb_mac_pmi", "PMI component", ["rnti", "index"]).labels(rnti=current_rnti, index=str(i)).set(int(v))

                    if match := re.search(r"UE .*? dlsch_rounds (\d+)/(\d+)/(\d+)/(\d+), dlsch_errors (\d+), pucch0_DTX (\d+)(?: \(SNR ([+-]?\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?: dB)?\))?(?: RSSI -?\d+(?:\.\d+)?)?, BLER ([\d.]+) MCS \((\d+)\) (\d+)(?: \(Qm \d+\))? CCE fail (\d+)(?:, goodput ([\d.]+) Mbps)?", line):
                        get_or_create_metric("oai_gnb_mac_dlsch_rounds_a", "DLSCH HARQ Round A", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(1)))
                        get_or_create_metric("oai_gnb_mac_dlsch_rounds_b", "DLSCH HARQ Round B", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(2)))
                        get_or_create_metric("oai_gnb_mac_dlsch_rounds_c", "DLSCH HARQ Round C", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(3)))
                        get_or_create_metric("oai_gnb_mac_dlsch_rounds_d", "DLSCH HARQ Round D", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(4)))
                        get_or_create_metric("oai_gnb_mac_dlsch_errors", "DLSCH Errors", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(5)))
                        get_or_create_metric("oai_gnb_mac_pucch0_dtx", "PUCCH0 DTX", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(6)))

                        if match.group(7) is not None:
                            get_or_create_metric("oai_gnb_mac_pucch_snr_avg_db", "Average PUCCH SNR", ["rnti"]).labels(rnti=current_rnti).set(float(match.group(7)))
                            get_or_create_metric("oai_gnb_mac_pucch_snr_delta_db", "Difference between measured and target PUCCH SNR", ["rnti"]).labels(rnti=current_rnti).set(float(match.group(8)))

                        get_or_create_metric("oai_gnb_mac_dl_bler", "DLSCH BLER", ["rnti"]).labels(rnti=current_rnti).set(float(match.group(9)))
                        get_or_create_metric("oai_gnb_mac_dl_mcs", "DLSCH MCS", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(11)))
                        get_or_create_metric("oai_gnb_mac_cce_fail", "CCE failures", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(12)))
                        if match.group(13) is not None:
                            get_or_create_metric("oai_gnb_mac_dl_goodput_mbps","DL goodput Mbps",["rnti"]).labels(rnti=current_rnti).set(float(match.group(13)))
                        if rssi := re.search(r"\) RSSI (-?\d+(?:\.\d+)?), BLER", line):
                            get_or_create_metric("oai_gnb_mac_pucch_rssi", "PUCCH RSSI", ["rnti"]).labels(rnti=current_rnti).set(float(rssi.group(1)))

                    if match := re.search(r"UE .*?ulsch_rounds (\d+)/(\d+)/(\d+)/(\d+), ulsch_errors (\d+), ulsch_DTX (\d+), BLER ([\d.]+) MCS \((\d+)\) (\d+) \(Qm (\d+) deltaMCS ([+-]?\d+(?:\.\d+)?)(?: dB)?\) NPRB (\d+) SNR ([+-]?\d+(?:\.\d+)?) \(([+-]?\d+(?:\.\d+)?)\)(?: dB)?(?: RSSI -?\d+(?:\.\d+)?)? CCE fail (\d+)(?:, goodput ([\d.]+) Mbps)?", line):
                        get_or_create_metric("oai_gnb_mac_ulsch_rounds_a", "ULSCH HARQ Round A", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(1)))
                        get_or_create_metric("oai_gnb_mac_ulsch_rounds_b", "ULSCH HARQ Round B", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(2)))
                        get_or_create_metric("oai_gnb_mac_ulsch_rounds_c", "ULSCH HARQ Round C", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(3)))
                        get_or_create_metric("oai_gnb_mac_ulsch_rounds_d", "ULSCH HARQ Round D", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(4)))
                        get_or_create_metric("oai_gnb_mac_ulsch_errors", "ULSCH Errors", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(5)))
                        get_or_create_metric("oai_gnb_mac_ulsch_dtx", "ULSCH DTX", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(6)))
                        get_or_create_metric("oai_gnb_mac_ul_bler", "ULSCH BLER", ["rnti"]).labels(rnti=current_rnti).set(float(match.group(7)))
                        get_or_create_metric("oai_gnb_mac_ul_mcs_index", "ULSCH MCS index in parentheses", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(8)))
                        get_or_create_metric("oai_gnb_mac_ul_mcs", "ULSCH MCS value", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(9)))
                        get_or_create_metric("oai_gnb_mac_qm", "Modulation Order Qm", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(10)))
                        get_or_create_metric("oai_gnb_mac_delta_mcs", "Delta MCS dB", ["rnti"]).labels(rnti=current_rnti).set(float(match.group(11)))
                        get_or_create_metric("oai_gnb_mac_nprb", "Allocated PRBs", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(12)))
                        get_or_create_metric("oai_gnb_mac_ul_snr_avg_db", "Average ULSCH SNR", ["rnti"]).labels(rnti=current_rnti).set(float(match.group(13)))
                        get_or_create_metric("oai_gnb_mac_ul_snr_delta_db", "Difference between measured and target ULSCH SNR", ["rnti"]).labels(rnti=current_rnti).set(float(match.group(14)))
                        get_or_create_metric("oai_gnb_mac_cce_fail","CCE Failures", ["rnti"]).labels(rnti=current_rnti).set(int(match.group(15)))
                        if match.group(16) is not None:
                            get_or_create_metric("oai_gnb_mac_ul_goodput_mbps", "UL Goodput", ["rnti"]).labels(rnti=current_rnti).set(float(match.group(16)))
                        if rssi := re.search(r"RSSI (-?\d+(?:\.\d+)?) CCE fail", line):
                            get_or_create_metric("oai_gnb_mac_ul_rssi", "ULSCH RSSI", ["rnti"]).labels(rnti=current_rnti).set(float(rssi.group(1)))

                    # Goodput on its own line: "UE 218f: LCID 1,2,4, goodput DL    0.00 UL    0.00 Mbps"
                    if match := re.search(r"goodput DL\s+([\d.]+)\s+UL\s+([\d.]+)\s+Mbps", line):
                        get_or_create_metric("oai_gnb_mac_dl_goodput_mbps","DL goodput Mbps",["rnti"]).labels(rnti=current_rnti).set(float(match.group(1)))
                        get_or_create_metric("oai_gnb_mac_ul_goodput_mbps", "UL Goodput", ["rnti"]).labels(rnti=current_rnti).set(float(match.group(2)))
                        # Active logical channels listed before goodput, e.g. "LCID 1,2,4,"
                        # (LCID 4 = first DRB, used to count UEs with an established data bearer)
                        if lcids := re.search(r"LCID ([\d,]+), goodput", line):
                            for lcid in filter(None, lcids.group(1).split(",")):
                                get_or_create_metric("oai_gnb_mac_lcid_active", "Logical channel present for this UE (1 = listed in MAC stats)", ["rnti", "lcid"]).labels(rnti=current_rnti, lcid=lcid).set(1)

                    if match := re.search(r"LCID (\d+): TX\s+(\d+)\s+RX\s+(\d+)", line):
                        get_or_create_metric("oai_gnb_mac_lcid_tx_bytes", "LCID TX Bytes", ["rnti", "lcid"]).labels(rnti=current_rnti, lcid=match.group(1)).set(int(match.group(2)))
                        get_or_create_metric("oai_gnb_mac_lcid_rx_bytes", "LCID RX Bytes", ["rnti", "lcid"]).labels(rnti=current_rnti, lcid=match.group(1)).set(int(match.group(3)))
                        get_or_create_metric("oai_gnb_mac_lcid_active", "Logical channel present for this UE (1 = listed in MAC stats)", ["rnti", "lcid"]).labels(rnti=current_rnti, lcid=match.group(1)).set(1)
    except Exception as e:
        print(f"[ERROR] MAC parsing failed: {e}")

    # RRC stats
    try:
        if os.path.isfile(log_paths["rrc"]):
            with open(log_paths["rrc"], "r") as f:
                lines = f.readlines()

                # Parse gNB-level parameters after UE blocks
                for line in lines:
                    # SSB ARFCN
                    if match := re.search(r"SSB ARFCN (\d+)", line):
                        get_or_create_metric("oai_gnb_rrc_ssb_arfcn", "SSB ARFCN", []).set(int(match.group(1)))
                    # ARFCN and SCS
                    if match := re.search(r"ARFCN (\d+) SCS (\d+) \(kHz\)", line):
                        get_or_create_metric("oai_gnb_rrc_arfcn", "ARFCN", []).set(int(match.group(1)))
                        get_or_create_metric("oai_gnb_rrc_scs_khz", "Subcarrier Spacing (kHz)", []).set(int(match.group(2)))
    except Exception as e:
        print(f"[ERROR] RRC parsing failed: {e}")

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            parse_logs()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.end_headers()
            self.wfile.write(generate_latest(registry))
        else:
            self.send_response(404)
            self.end_headers()

def start_server(port=9090):
    print(f"Starting Prometheus exporter on port {port}")
    server = HTTPServer(('0.0.0.0', port), MetricsHandler)
    server.serve_forever()

if __name__ == '__main__':
    start_server()
