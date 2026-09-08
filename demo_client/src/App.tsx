import { ConnectedPlot, type SourceConfig } from '@diamondlightsource/davidia';
import { useState } from 'react';
import './App.css';

function MJPEGView(props: { hostname: string, port: string, pvName: string }) {
  const url = `http://${props.hostname}:${props.port}/mjpg/${props.pvName}`;
  return (
    <div>
      <img src={url} height={480} />
    </div>
  );
}

interface PVASourceConfig extends SourceConfig {
  pvName: string,
  minPeriod: number,
}

function App() {
  const defPVName = "ws523-AD-SIM-01:PVA:ARRAY";
  const defSrcConfig: PVASourceConfig = { plugin: "PVASourcePlugin", pvName: defPVName, minPeriod: 0.1, activate: false };
  const [pvName, setPVName] = useState(defPVName);
  const [activate, setActivate] = useState(false);
  const [mjpeg, setMJPEG] = useState(false);
  const plots = ['plot_0'];
  const host = "127.0.0.1";
  const port = '9000';
  const uuid = crypto.randomUUID().slice(-8);

  return (
    <>
      <div className={"center"}>
        <label>
          PV name <textarea cols={40} rows={1} value={pvName} onChange={(e) => setPVName(e.target.value.toLocaleUpperCase())} />
        </label>
        {mjpeg ? <MJPEGView hostname={host} port={port} pvName={pvName} /> : <ConnectedPlot
          plotId={plots[0]}
          uuid={uuid}
          hostname={host}
          port={port}
          tightAxes={true}
          source={{ ...defSrcConfig, pvName, activate }}
          customToolbarChildren={null}
        />}
        <button
          onClick={() => setActivate((a) => !a)}
        >
          {activate ? 'Disable' : 'Enable'} source
        </button>
        <button
          onClick={() => setMJPEG((a) => !a)}
        >
          {mjpeg ? 'MJPEG' : 'Davidia'} view
        </button>
      </div >
    </>
  )
}

export default App
