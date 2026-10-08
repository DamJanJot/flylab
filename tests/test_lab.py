"""Stage-7 API boundaries, shared clocks, and worker control without GPU work."""

import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from flylab import write_json
from flywire_data import file_sha256
from lab_data import Catalog, experiment_request, recording_payload, safe_key, write_live_json
from lab_server import Server, byte_range, Jobs
from lab_worker import Control


REQUEST = {"scenario": "left", "condition": "connected", "seed": 42, "duration_s": 0.8, "stimulation_hz": 150}


class RequestTests(unittest.TestCase):
    def test_bounded_request_does_not_change_baseline(self):
        config, trial = experiment_request(REQUEST)
        self.assertEqual(trial.seed, 42)
        self.assertEqual(config["simulation"]["sensors"]["duration_s"], .8)
        self.assertEqual(config["simulation"]["coupling"]["baseline_input_hz"], 20)
        config, _ = experiment_request({**REQUEST, "seed": 2**32-1, "duration_s": 2})
        self.assertEqual(config["seeds"], [2**32-1, 0])

    def test_bad_input_and_stimulation_constraints(self):
        for key, value in (("seed", True), ("seed", -1), ("seed", 2**32), ("scenario", "../x"),
                           ("duration_s", .001), ("duration_s", float("nan")), ("condition", "stimulate_left"),
                           ("stimulation_hz", float("inf")), ("stimulation_hz", 400)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                experiment_request({**REQUEST, key:value})
        with self.assertRaises(ValueError):
            experiment_request({**REQUEST, "command": "anything"})
        config, trial = experiment_request({**REQUEST, "scenario":"neutral", "condition":"stimulate_right"})
        self.assertEqual(trial.condition, "stimulate_right")

    def test_no_paths_as_identifiers(self):
        for value in ("../x", "a/b", "C:\\Users", "%2e", "a.b", "", None):
            with self.assertRaises(ValueError):
                safe_key(value)

    def test_ranges(self):
        self.assertEqual(byte_range(None, 100), (0,99,False))
        self.assertEqual(byte_range("bytes=4-12",100),(4,12,True))
        self.assertEqual(byte_range("bytes=90-",100),(90,99,True))
        self.assertEqual(byte_range("bytes=-10",100),(90,99,True))
        self.assertEqual(byte_range("bytes=95-500",100),(95,99,True))
        for value in ("bytes=100-", "bytes=4-2", "bytes=-0", "bytes=1-2,4-5", "garbage"):
            with self.assertRaises(ValueError): byte_range(value,100)


class ArchiveTests(unittest.TestCase):
    def test_tamper_detection_and_unknown_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); directory=root/"trials"/"left-connected-seed42";directory.mkdir(parents=True)
            write_json(directory/"trial.json", {"ok":True})
            write_json(directory/"result.json", {"technical_status":"passed", "checks":{"a":True},
                                                "files":{"trial.json":file_sha256(directory/"trial.json")}})
            write_json(root/"manifest.json", {"completed":{"left-connected-seed42":file_sha256(directory/"result.json")}})
            catalog=Catalog(root,root/"jobs")
            catalog.locate("archive_left-connected-seed42")
            with self.assertRaises(FileNotFoundError):catalog.locate("archive_missing")
            write_json(directory/"trial.json", {"ok":False})
            with self.assertRaises(ValueError):catalog.locate("archive_left-connected-seed42")

    def test_payload_ids_and_two_clocks(self):
        from closed_loop_model import CELLS
        times=np.array([0,.01,.02])
        data={key:np.zeros((2,4)) for key in ("counts","input_rates_hz","voltage_v")}
        data.update({key:np.zeros((3,2)) for key in ("panels","joint_rad","joint_velocity_rad_s")})
        data.update(time_s=times, bin_start_s=times[:-1],bin_end_s=times[1:],contacts=np.zeros((3,6)),
                    neuron_ids=np.array([row[0] for row in CELLS]),input_ids=np.array([row[0] for row in CELLS]),
                    spike_times_s=np.array([.015]),spike_indices=np.array([2]),applied_command=np.ones((2,2)),
                    next_command=np.ones((2,2)),filtered_dn_hz=np.zeros((2,2)),thorax_m=np.ones((3,3))*.001,
                    heading_rad=np.array([0,.1,.2]),ommatidia=np.ones((3,2,721,2))*.5)
        result=recording_payload(data)
        self.assertEqual(result["neuron_ids"][0],CELLS[0][0])
        self.assertEqual(result["bin_end_s"],[.01,.02])
        self.assertEqual(result["position_mm"][0],[1,1,1])
        self.assertEqual(result["retina"][0][0],[.5]*721)
        self.assertEqual(len(result["voltage_mv"]),len(result["time_s"])-1)


class ControlTests(unittest.TestCase):
    def test_atomic_status_retries_sharing_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/"status.json"
            write_json(target,{"old":True})
            original=Path.replace
            attempts=[]
            def replace(p,destination):
                attempts.append(1)
                if len(attempts)==1:
                    self.assertEqual(json.loads(target.read_text()),{"old":True})
                    raise PermissionError("sharing violation")
                return original(p,destination)
            with patch.object(Path,"replace",replace):write_live_json(target,{"new":True})
            self.assertEqual(len(attempts),2)
            self.assertEqual(json.loads(target.read_text()),{"new":True})

    def test_pause_freezes_until_resume_and_cancel_interrupts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write_json(root/"control.json",{"action":"pause"})
            control=Control(root);finished=threading.Event()
            def run():control.checkpoint(.4);finished.set()
            thread=threading.Thread(target=run);thread.start()
            try:
                for _ in range(100):
                    if (root/"status.json").exists():break
                    time.sleep(.01)
                self.assertFalse(finished.wait(.15))
                self.assertEqual(json.loads((root/"status.json").read_text())["state"],"paused")
            finally:
                from behavior_experiment import write_checkpoint
                write_checkpoint(root/"control.json",{"action":"resume"});thread.join(3)
            self.assertTrue(finished.is_set())
            write_json(root/"control.json",{"action":"cancel"})
            with self.assertRaises(InterruptedError):control.checkpoint(.5)

    def test_no_control_without_active_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs=Jobs(Catalog(jobs=tmp))
            self.assertEqual(jobs.status()["state"],"idle")
            with self.assertRaises(RuntimeError):jobs.control("pause")
            with self.assertRaises(ValueError):jobs.control("delete")


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.server=Server(0,Catalog(jobs=cls.temp.name))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()

    def request(self,path,method="GET",body=None,headers=None):
        connection=http.client.HTTPConnection("127.0.0.1",self.server.server_port)
        connection.request(method,path,body=body,headers=headers or {})
        response=connection.getresponse();payload=response.read();status=response.status
        connection.close();return status,payload

    def test_static_and_bootstrap(self):
        self.assertEqual(self.request("/")[0],200)
        status,data=self.request("/api/bootstrap")
        self.assertEqual(status,200);self.assertEqual(json.loads(data)["token"],self.server.token)
        self.assertEqual(self.request("/../lab_server.py")[0],404)

    def test_host_and_cross_origin_rejected(self):
        self.assertEqual(self.request("/api/bootstrap",headers={"Host":"evil.test"})[0],403)
        self.assertEqual(self.request("/api/control","POST","{}",{"Content-Type":"application/json"})[0],403)
        headers={"Origin":"https://evil.test","X-Lab-Token":self.server.token,"Content-Type":"application/json"}
        self.assertEqual(self.request("/api/jobs","POST","{}",headers)[0],403)

    def test_authorized_validation_and_no_active_control(self):
        headers={"Origin":f"http://127.0.0.1:{self.server.server_port}","X-Lab-Token":self.server.token,"Content-Type":"application/json"}
        self.assertEqual(self.request("/api/jobs","POST",'{"kind":"simulate","parameters":{}}',headers)[0],400)
        self.assertEqual(self.request("/api/control","POST",'{"action":"pause"}',headers)[0],409)
        self.assertEqual(self.request("/api/recordings/%2e%2e%2fsecret")[0],422)


if __name__ == "__main__":
    unittest.main()
