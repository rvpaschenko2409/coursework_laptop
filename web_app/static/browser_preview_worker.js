let python;

async function initialize(input) {
  importScripts('./pyodide/pyodide.js');
  python = await loadPyodide({indexURL: new URL('./pyodide/', self.location.href).href});
  await python.loadPackage('pandas');
  python.FS.mkdir('/home/pyodide/decision_core');
  for (const [name, source] of Object.entries(input.sources)) {
    python.FS.writeFile('/home/pyodide/decision_core/' + name, source);
  }
  python.globals.set('input_json', JSON.stringify(input));
  python.runPython(`
import json
import sys
import pandas as pd

sys.path.insert(0, '/home/pyodide')
from decision_core.marcos import calculate_marcos
from decision_core.vikor import calculate_vikor
from decision_core.preview import compare, perturb

data = json.loads(input_json)
matrix = pd.DataFrame(**data['matrix'])
if 'directions' in data:
    matrix.attrs['directions'] = data['directions']

def rows(frame):
    return frame.rename_axis('alternative_code').reset_index().to_dict(orient='records')

if 'project_id' in data:
    from decision_core.merec import calculate_merec
    base = calculate_merec(matrix).set_index('criterion')['weight']
    data['weights'] = base.to_dict()
    data['rankings'] = {
        'marcos': rows(calculate_marcos(matrix, base)),
        'vikor': rows(calculate_vikor(matrix, base, v=0.5)[0]),
    }

def preview(request_json):
    criterion, change, v, *options = json.loads(request_json)
    base_weights = data['weights']
    if options and options[0] and 'project_id' not in data:
        from decision_core.merec import calculate_merec
        base_weights = calculate_merec(matrix).set_index('criterion')['weight'].to_dict()
    weights = perturb(base_weights, criterion, change / 100)
    series = pd.Series(weights)
    marcos = rows(calculate_marcos(matrix, series))
    vikor_frame, conditions = calculate_vikor(matrix, series, v=v)
    vikor = rows(vikor_frame)
    return json.dumps({
        'weights': weights,
        'marcos': marcos,
        'vikor': vikor,
        'conditions': conditions,
        'changes': {
            'marcos': compare(marcos, data['rankings']['marcos']),
            'vikor': compare(vikor, data['rankings']['vikor']),
        },
    }, default=lambda value: value.item(), allow_nan=False)
  `);
}

self.onmessage = async ({data}) => {
  try {
    if (data.input) {
      await initialize(data.input);
      self.postMessage({id: data.id});
    } else {
      python.globals.set('request_json', JSON.stringify(data.scenario));
      const result = JSON.parse(python.runPython('preview(request_json)'));
      self.postMessage({id: data.id, result});
    }
  } catch {
    self.postMessage({id: data.id, error: true});
  }
};
