import React, { useMemo, useState } from 'react';
import { Button } from 'react-bootstrap';

interface Rule {
  body: string[];
  head: string[];
  body_fids: string[];
  head_fids: string[];
  confidence: number;
  support: number;
}

interface Pattern {
  id: string;
  kind: string;
  anchor: string;
  node_labels: string[];
  rel_types: (string | null)[];
}

interface RuleStructure {
  body: { [key: string]: number };
  head: { [key: string]: number };
}

interface AggregatedRule {
  structure: RuleStructure;
  rules: Rule[];
  count: number;
}

interface RuleStructureListProps {
  rules: Rule[];
  onShowInstances: (selectedRules: Rule[]) => void;
  fidToPidMap: { [key: string]: string };
  patterns: { [key: string]: Pattern };
}

const getPatternStructureStr = (pattern: Pattern | undefined): string => {
  if (!pattern) return "Unknown Pattern";
  const structure: string[] = [];
  if (pattern.kind === 'anyrel') {
      structure.push(`(${pattern.anchor})-[*ANYREL*]->(${pattern.node_labels[0]})`);
  } else {
      pattern.node_labels.forEach((lbl: string, i: number) => {
          if (i < pattern.rel_types.length) {
              const rel = pattern.rel_types[i];
              structure.push(`(${lbl})-[${rel}]->`);
          } else {
              structure.push(`(${lbl})`);
          }
      });
  }
  return structure.join('');
};

const RuleStructureList: React.FC<RuleStructureListProps> = ({ rules, onShowInstances, fidToPidMap, patterns }) => {
  const [selectedStructures, setSelectedStructures] = useState<string[]>([]);

  const aggregatedRules = useMemo(() => {
    const aggregation: { [key: string]: AggregatedRule } = {};

    for (const rule of rules) {
      const bodyCounts: { [key: string]: number } = {};
      for (const fid of rule.body_fids) {
        const pid = fidToPidMap[fid];
        if (pid && patterns[pid]) {
          const patternStruct = getPatternStructureStr(patterns[pid]);
          bodyCounts[patternStruct] = (bodyCounts[patternStruct] || 0) + 1;
        }
      }

      const headCounts: { [key: string]: number } = {};
      for (const fid of rule.head_fids) {
        const pid = fidToPidMap[fid];
        if (pid && patterns[pid]) {
          const patternStruct = getPatternStructureStr(patterns[pid]);
          headCounts[patternStruct] = (headCounts[patternStruct] || 0) + 1;
        }
      }

      const bodyKey = Object.entries(bodyCounts).map(([p, c]) => `${p}(x${c})`).sort().join(',');
      const headKey = Object.entries(headCounts).map(([p, c]) => `${p}(x${c})`).sort().join(',');
      const structureKey = `B:[${bodyKey}]H:[${headKey}]`;

      if (!aggregation[structureKey]) {
        aggregation[structureKey] = {
          structure: { body: bodyCounts, head: headCounts },
          rules: [],
          count: 0,
        };
      }
      aggregation[structureKey].rules.push(rule);
      aggregation[structureKey].count++;
    }

    return Object.values(aggregation);
  }, [rules, fidToPidMap, patterns]);

  const handleSelectionChange = (structureKey: string) => {
    setSelectedStructures(prev =>
      prev.includes(structureKey)
        ? prev.filter(s => s !== structureKey)
        : [...prev, structureKey]
    );
  };

  const handleShowInstances = () => {
    const selected: Rule[] = [];
    aggregatedRules.forEach(aggRule => {
        const bodyKey = Object.entries(aggRule.structure.body).map(([p, c]) => `${p}(x${c})`).sort().join(',');
        const headKey = Object.entries(aggRule.structure.head).map(([p, c]) => `${p}(x${c})`).sort().join(',');
        const structureKey = `B:[${bodyKey}]H:[${headKey}]`;
      if (selectedStructures.includes(structureKey)) {
        selected.push(...aggRule.rules);
      }
    });
    onShowInstances(selected);
  };

  return (
    <div>
      <h5>Aggregated Rule Structures</h5>
      <Button
        variant="primary"
        className="mb-3"
        disabled={selectedStructures.length === 0}
        onClick={handleShowInstances}
      >
        Show Rule Instances
      </Button>
      <div className="list-group">
        {aggregatedRules.map((aggRule, index) => {
            const bodyKey = Object.entries(aggRule.structure.body).map(([p, c]) => `${p}(x${c})`).sort().join(',');
            const headKey = Object.entries(aggRule.structure.head).map(([p, c]) => `${p}(x${c})`).sort().join(',');
            const structureKey = `B:[${bodyKey}]H:[${headKey}]`;

          return (
            <div key={structureKey} className="list-group-item d-flex justify-content-between align-items-center">
              <input
                type="checkbox"
                className="form-check-input me-3"
                onChange={() => handleSelectionChange(structureKey)}
                checked={selectedStructures.includes(structureKey)}
              />
              <div className="flex-grow-1">
                <div className="row">
                  <div className="col-md-5">
                    <strong>Body:</strong>
                    <ul className="list-unstyled">
                      {Object.entries(aggRule.structure.body).map(([p, c]) => <li key={p}>{p} (x{c})</li>)}
                    </ul>
                  </div>
                  <div className="col-md-2 text-center align-self-center">=&gt;</div>
                  <div className="col-md-5">
                    <strong>Head:</strong>
                    <ul className="list-unstyled">
                      {Object.entries(aggRule.structure.head).map(([p, c]) => <li key={p}>{p} (x{c})</li>)}
                    </ul>
                  </div>
                </div>
              </div>
              <span className="badge bg-secondary rounded-pill">{aggRule.count}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default RuleStructureList;