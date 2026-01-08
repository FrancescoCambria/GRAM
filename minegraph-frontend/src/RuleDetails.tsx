import React, { useState, useEffect, useMemo } from 'react';
import { Button } from 'react-bootstrap';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || 'http://localhost:8081';

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

interface RuleDetailsProps {
  rules: Rule[];
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


const RuleDetails: React.FC<RuleDetailsProps> = ({ rules, fidToPidMap, patterns }) => {
  const [nodeNames, setNodeNames] = useState<{ [key: string]: string }>({});
  const [sortBy, setSortBy] = useState<'support' | 'confidence' | null>(null);
  const [sortDesc, setSortDesc] = useState(true);

  useEffect(() => {
    const fetchNodeNames = async () => {
      const allFids = new Set<string>();
      for (const rule of rules) {
        rule.body_fids.forEach(fid => allFids.add(fid));
        rule.head_fids.forEach(fid => allFids.add(fid));
      }

      if (allFids.size > 0) {
        try {
          const response = await fetch(`${BACKEND_URL}/api/node-names`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({ node_ids: Array.from(allFids) }),
          });
          const names = await response.json();
          setNodeNames(names);
        } catch (error) {
          console.error('Error fetching node names:', error);
        }
      }
    };

    fetchNodeNames();
  }, [rules]);

  const sortedRules = useMemo(() => {
    if (!rules) return [];
    const r = [...rules];
    if (sortBy) {
      r.sort((a, b) => {
        const valA = a[sortBy];
        const valB = b[sortBy];
        return sortDesc ? valB - valA : valA - valB;
      });
    }
    return r;
  }, [rules, sortBy, sortDesc]);

  const handleSort = (criteria: 'support' | 'confidence') => {
    if (sortBy === criteria) {
      setSortDesc(!sortDesc);
    } else {
      setSortBy(criteria);
      setSortDesc(true);
    }
  };

  if (!rules || rules.length === 0) {
    return <p>No rule instances to display.</p>;
  }

  return (
    <div style={{ maxHeight: '500px', overflowY: 'auto', position: 'relative' }}>
       <div className="d-flex justify-content-end mb-2 sticky-top bg-white pt-2 pb-2 border-bottom" style={{ zIndex: 10, top: 0 }}>
          <div className="me-2 align-self-center">Sort by:</div>
          <Button 
            variant={sortBy === 'support' ? "primary" : "outline-primary"} 
            size="sm" 
            className="me-1"
            onClick={() => handleSort('support')}
          >
            Support {sortBy === 'support' && (sortDesc ? '↓' : '↑')}
          </Button>
          <Button 
            variant={sortBy === 'confidence' ? "primary" : "outline-primary"} 
            size="sm"
            onClick={() => handleSort('confidence')}
          >
            Confidence {sortBy === 'confidence' && (sortDesc ? '↓' : '↑')}
          </Button>
      </div>
      {sortedRules.map((rule, index) => {
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

        return (
          <div key={index} className="card mb-3">
            <div className="card-header d-flex justify-content-between">
              <strong>Rule {index + 1}</strong>
              <span>
                Confidence: {rule.confidence.toFixed(2)} | Support: {rule.support.toFixed(4)}
              </span>
            </div>
            <div className="card-body">
              <div className="row">
                <div className="col-md-5">
                  <h6>Body</h6>
                  <ul className="list-group list-group-flush">
                    {Object.entries(bodyCounts).map(([pattern, count]) => (
                      <li key={pattern} className="list-group-item">
                        {`${pattern} (x${count})`}
                      </li>
                    ))}
                  </ul>
                  <h6 className="mt-3">Instances:</h6>
                  <ul className="list-group list-group-flush">
                    {rule.body_fids.map(fid => (
                      <li key={fid} className="list-group-item list-group-item-light">
                        <small>{nodeNames[fid] || `ID: ${fid}`}</small>
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="col-md-2 text-center align-self-center">
                  <span style={{ fontSize: '2rem' }}>=&gt;</span>
                </div>
                <div className="col-md-5">
                  <h6>Head</h6>
                  <ul className="list-group list-group-flush">
                    {Object.entries(headCounts).map(([pattern, count]) => (
                      <li key={pattern} className="list-group-item">
                        {`${pattern} (x${count})`}
                      </li>
                    ))}
                  </ul>
                  <h6 className="mt-3">Instances:</h6>
                  <ul className="list-group list-group-flush">
                    {rule.head_fids.map(fid => (
                       <li key={fid} className="list-group-item list-group-item-light">
                        <small>{nodeNames[fid] || `ID: ${fid}`}</small>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default RuleDetails;
