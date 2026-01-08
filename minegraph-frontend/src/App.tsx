import React, { useState, useEffect, useRef } from 'react';
import { Container, Row, Col, Card, Form, Button, ListGroup, OverlayTrigger, Tooltip, Modal, Spinner, InputGroup } from 'react-bootstrap';
import CytoscapeComponent from 'react-cytoscapejs';
import cytoscape from 'cytoscape';
import 'bootstrap-icons/font/bootstrap-icons.css';
import './App.css';
import SequenceList from './PatternList';
import RuleDetails from './RuleDetails';
import CombinationResults from './CombinationResults';
import RuleStructureList from './RuleStructureList';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || 'http://localhost:8081';

interface Sequence {
  id: string;
  kind: string;
  anchor: string;
  node_labels: string[];
  rel_types: (string | null)[];
}

interface CytoscapeElement {
  data: {
    id: string;
    label?: string;
    source?: string;
    target?: string;
    color?: string;
  };
}

function App() {
  const [graphData, setGraphData] = useState<CytoscapeElement[]>([]);
  const [nodeLabels, setNodeLabels] = useState<string[]>([]);
  const [edgeTypes, setEdgeTypes] = useState<string[]>([]);
  const [selectedNodeLabels, setSelectedNodeLabels] = useState<string[]>([]);
  const [selectedEdgeTypes, setSelectedEdgeTypes] = useState<string[]>([]);
  const [anchors, setAnchors] = useState<string[]>([]);
  const [support, setSupport] = useState(0.1);
  const [confidence, setConfidence] = useState(0.5);
  const [maxLength, setMaxLength] = useState(3);
  const [maxCombinationSize, setMaxCombinationSize] = useState(3);
  const [analysisAnchors, setAnalysisAnchors] = useState<any>(null);
  const [forestData, setForestData] = useState<any>(null);
  const [sequences, setSequences] = useState<any>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [searchStatus, setSearchStatus] = useState<'searching' | 'success' | 'error'>('searching');
  const [selectedSequenceIds, setSelectedSequenceIds] = useState<string[]>([]);
  const [combinedResults, setCombinedResults] = useState<any>(null);
  const [isCombining, setIsCombining] = useState(false);
  const [rules, setRules] = useState<any[] | null>(null);
  const [isFetchingRules, setIsFetchingRules] = useState(false);
  const [rulesToShow, setRulesToShow] = useState<any[]>([]);
  const [fidToPidMap, setFidToPidMap] = useState<{ [key: string]: string }>({});
  const cyRef = useRef<cytoscape.Core | null>(null);
  const [isLoadingSchema, setIsLoadingSchema] = useState(true);
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [isSuggestedEdgesMode, setIsSuggestedEdgesMode] = useState(false);
  const [conditions, setConditions] = useState<{ [key: string]: string[] }>({});
  const [newConditionLabel, setNewConditionLabel] = useState<string>('');
  const [newConditionValue, setNewConditionValue] = useState<string>('');
  const [showConditionsModal, setShowConditionsModal] = useState(false);

  useEffect(() => {
    const fetchSchema = async () => {
      setIsLoadingSchema(true);
      setSchemaError(null);
      try {
        const response = await fetch(`${BACKEND_URL}/api/schema`);
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        if (data.error) {
          throw new Error(data.error);
        }

        if (!data.elements || data.elements.length === 0) {
            setSchemaError("Could not connect to the database or the schema is empty.");
            setGraphData([]);
            setNodeLabels([]);
            setEdgeTypes([]);
        } else {
            setGraphData(data.elements);
            const nodeLabelsFromData: string[] = [];
            const edgeTypesFromData: string[] = [];
            data.elements.forEach((el: CytoscapeElement) => {
                if (el.data.label) {
                    if (el.data.source) {
                        edgeTypesFromData.push(el.data.label);
                    } else {
                        nodeLabelsFromData.push(el.data.label);
                    }
                }
            });
            setNodeLabels([...new Set(nodeLabelsFromData)]);
            setEdgeTypes([...new Set(edgeTypesFromData)]);
        }
      } catch (error: any) {
        console.error('Error fetching schema:', error);
        setSchemaError(error.message || 'An unknown error occurred.');
      } finally {
        setIsLoadingSchema(false);
      }
    };

    fetchSchema();
  }, []);

  useEffect(() => {
    if (isSuggestedEdgesMode) {
      const validEdges = new Set<string>();
      const idToLabel = new Map<string, string>();

      graphData.forEach(el => {
        if (!el.data.source && el.data.label) { // Node
          idToLabel.set(el.data.id, el.data.label);
        }
      });

      graphData.forEach(el => {
        if (el.data.source && el.data.target && el.data.label) { // Edge
          const sourceLabel = idToLabel.get(el.data.source);
          const targetLabel = idToLabel.get(el.data.target);
          if (sourceLabel && targetLabel &&
            selectedNodeLabels.includes(sourceLabel) &&
            selectedNodeLabels.includes(targetLabel)) {
            validEdges.add(el.data.label);
          }
        }
      });

      setSelectedEdgeTypes(Array.from(validEdges));
    }
  }, [selectedNodeLabels, isSuggestedEdgesMode, graphData]);

  const handleRunAnalysis = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/analyze`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          node_labels: selectedNodeLabels,
          edge_types: selectedEdgeTypes,
          anchors: anchors,
          support: support,
          confidence: confidence,
          max_length: maxLength,
          conditions: conditions,
        }),
      });
      const data = await response.json();
      setAnalysisAnchors(data.anchor_counts);
      setForestData({ forest: data.forest, patterns: data.patterns });
      setSequences(null);
      setSelectedSequenceIds([]);
      setCombinedResults(null);
      setRules(null);
    } catch (error) {
      console.error('Error running analysis:', error);
    }
  };

  const handleSelectedSequencesChange = (sequenceIds: string[]) => {
    setSelectedSequenceIds(sequenceIds);
  };

  const handleCombineSequences = async () => {
    setIsCombining(true);
    setCombinedResults(null);
    setRules(null);
    try {
      const response = await fetch(`${BACKEND_URL}/api/combine`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          pattern_ids: selectedSequenceIds,
          max_combination_size: maxCombinationSize,
        }),
      });
      const data = await response.json();
      setCombinedResults(data);
    } catch (error) {
      console.error('Error combining sequences:', error);
      alert('Sequence combination failed. See console for details.');
    } finally {
      setIsCombining(false);
    }
  };

  const handleSelectCombination = async (combinationGroup: any) => {
    const newFidToPidMap: { [key: string]: string } = {};
    if (combinationGroup.combinations) {
      for (const combo of combinationGroup.combinations) {
        if (combo.items) {
          for (const item of combo.items) {
            const [pid, fid] = item.split('::');
            if (pid && fid) {
              newFidToPidMap[fid] = pid;
            }
          }
        }
      }
    }
    setFidToPidMap(newFidToPidMap);

    setIsFetchingRules(true);
    setRules(null);
    try {
      const response = await fetch(`${BACKEND_URL}/api/rules`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          combinations: combinationGroup.combinations,
        }),
      });
      const data = await response.json();
      setRules(data);
    } catch (error) {
      console.error('Error fetching rules:', error);
      alert('Rule generation failed. See console for details.');
    } finally {
      setIsFetchingRules(false);
    }
  };

  const handleShowRuleInstances = (selectedRules: any[]) => {
    setRulesToShow(selectedRules);
  };

  const handleRecenter = () => {
    if (cyRef.current) {
      cyRef.current.fit();
      cyRef.current.center();
    }
  };

  const handleDownloadForest = () => {
    if (!forestData) return;
    
    const content = JSON.stringify(forestData, null, 2);
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'forest_dictionary.txt';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const handleDownloadCSV = (endpoint: string, filename: string) => {
      // Use window.open or fetch to get the file. 
      // Fetch is better to handle errors, but window.open is simpler for downloads.
      // However, Flask Response with Content-disposition header should work with window.location.href or window.open
      window.location.href = `${BACKEND_URL}/api/${endpoint}`;
  };

  const handleAnchorClick = async (anchor: string, length: number) => {
    setIsSearching(true);
    setSearchStatus('searching');
    setSequences(null);
    setSelectedSequenceIds([]);
    setCombinedResults(null);
    setRules(null);
    setRulesToShow([]);
    try {
      const response = await fetch(`${BACKEND_URL}/api/patterns`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          anchor: anchor,
          length: length,
        }),
      });
      const data = await response.json();
      setSequences(data);
      setSearchStatus('success');
      setTimeout(() => {
        setIsSearching(false);
      }, 1500);
    } catch (error) {
      console.error('Error fetching sequences:', error);
      setSearchStatus('error');
      setTimeout(() => {
        setIsSearching(false);
      }, 3000);
    }
  };
  const handleNodeLabelSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const { value, checked } = event.target;
    if (!checked) {
      setAnchors(prev => prev.filter(item => item !== value));
    }
    setSelectedNodeLabels(prev => checked ? [...prev, value] : prev.filter(item => item !== value));
  };

  const handleEdgeTypeSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    setIsSuggestedEdgesMode(false);
    const { value, checked } = event.target;
    setSelectedEdgeTypes(prev => checked ? [...prev, value] : prev.filter(item => item !== value));
  };

  const handleAnchorSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const { value, checked } = event.target;
    setAnchors(prev => checked ? [...prev, value] : prev.filter(item => item !== value));
  };

  const handleSupportChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = parseFloat(e.target.value);
    if (value < 0) {
      setSupport(0);
    } else if (value > 1) {
      setSupport(1);
    } else {
      setSupport(value);
    }
  };

  const handleConfidenceChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = parseFloat(e.target.value);
    if (value < 0) {
      setConfidence(0);
    } else if (value > 1) {
      setConfidence(1);
    } else {
      setConfidence(value);
    }
  };

  const handleMaxLengthChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = parseInt(e.target.value);
    if (value < 0) {
      setMaxLength(0);
    } else {
      setMaxLength(value);
    }
  };

  const handleMaxCombinationSizeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = parseInt(e.target.value);
    if (value < 0) {
      setMaxCombinationSize(0);
    } else {
      setMaxCombinationSize(value);
    }
  };

  const handleAddCondition = () => {
    if (newConditionLabel && newConditionValue) {
      setConditions(prev => ({
        ...prev,
        [newConditionLabel]: [...(prev[newConditionLabel] || []), newConditionValue]
      }));
      setNewConditionValue('');
    }
  };

  const handleRemoveCondition = (label: string, value: string) => {
    setConditions(prev => {
      const newConditions = { ...prev };
      newConditions[label] = newConditions[label].filter(v => v !== value);
      if (newConditions[label].length === 0) {
        delete newConditions[label];
      }
      return newConditions;
    });
  };

  const stylesheet = [
    {
      selector: 'node',
      style: {
        'background-color': 'data(color)',
        'label': 'data(label)',
        'text-valign': 'center',
        'color': '#000',
        'font-size': '20px',
        'width': '80px',
        'height': '80px',
      }
    },
    {
      selector: 'edge',
      style: {
        'width': 2,
        'line-color': '#413f3fff',
        'target-arrow-color': '#413f3fff',
        'target-arrow-shape': 'triangle',
        'curve-style': 'bezier',
        'label': 'data(label)',
        'font-size': '14px',
        'color': '#000',
      }
    }
  ];

  const layout = {
    name: 'cose',
    idealEdgeLength: 100,
    nodeRepulsion: 4000,
    fit: true,
    padding: 30
  };

  return (
    <Container fluid>
      <h1 className="my-4">Graph Rule Association Miner</h1>
      <Row>
        <Col md={4}>
          <Card className="mb-4" style={{ height: '450px' }}>
            <Card.Header className="d-flex justify-content-between align-items-center fw-bold">
              Graph Schema
              <Button variant="outline-secondary" size="sm" onClick={handleRecenter} title="Recenter">
                <i className="bi bi-crosshair"></i>
              </Button>
            </Card.Header>
            <Card.Body>
              {isLoadingSchema ? (
                <p>Loading schema...</p>
              ) : schemaError ? (
                <div className="alert alert-danger">{schemaError}</div>
              ) : (
                <CytoscapeComponent
                  elements={graphData}
                  stylesheet={stylesheet}
                  style={{ width: '100%', height: '100%' }}
                  layout={layout}
                  cy={(cy: cytoscape.Core) => { cyRef.current = cy; }}
                />
              )}
            </Card.Body>
          </Card>
        </Col>
        <Col md={8}>
          <Card className="mb-4" style={{ height: '450px' }}>
            <Card.Header className="d-flex justify-content-between align-items-center fw-bold">
              Parameters
              <div style={{ height: '31px' }}></div> {/* Placeholder to match height of button in Graph Schema header */}
            </Card.Header>
            <Card.Body style={{ display: 'flex', flexDirection: 'column', height: '100%', paddingBottom: '10px' }}>
              <Form style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                <Row style={{ flex: 1, minHeight: 0 }}>
                  <Col md={8} className="d-flex flex-column" style={{ height: '100%' }}>
                      <Row style={{ flex: '0 1 auto', minHeight: 0 }}>
                          <Col md={6} className="d-flex flex-column" style={{ height: '100%', overflowY: 'auto' }}>
                            <div style={{ flex: '1 1 auto' }}>
                                <h5 className="mb-3">Node Labels</h5>
                                <Row className="mb-2 fw-bold small text-muted border-bottom pb-1 mx-0">
                                   <Col xs={3} className="text-center p-0">Include</Col>
                                   <Col xs={3} className="text-center p-0">Anchor</Col>
                                   <Col xs={6}>Label</Col>
                                </Row>
                                {nodeLabels.map(label => (
                                  <Row key={label} className="mb-1 align-items-center mx-0 border-bottom-subtle pb-1">
                                    <Col xs={3} className="text-center">
                                      <Form.Check
                                        type="checkbox"
                                        value={label}
                                        onChange={handleNodeLabelSelect}
                                        className="d-inline-block"
                                      />
                                    </Col>
                                    <Col xs={3} className="text-center">
                                      <Form.Check
                                        type="checkbox"
                                        value={label}
                                        checked={anchors.includes(label)}
                                        onChange={handleAnchorSelect}
                                        disabled={!selectedNodeLabels.includes(label)}
                                        className="d-inline-block"
                                      />
                                    </Col>
                                    <Col xs={6} style={{ whiteSpace: 'normal', wordBreak: 'break-word' }}>{label}</Col>
                                  </Row>
                                ))}
                            </div>
                          </Col>

                          <Col md={6} style={{ height: '100%', overflowY: 'auto', borderLeft: '1px solid #dee2e6' }}>
                             <div className="d-flex flex-column mb-3">
                              <h5 className="mb-2">Edges</h5>
                              <Button
                                variant={isSuggestedEdgesMode ? "success" : "secondary"}
                                size="sm"
                                onClick={() => setIsSuggestedEdgesMode(!isSuggestedEdgesMode)}
                                style={{ fontSize: '0.7rem' }}
                              >
                                Suggest
                              </Button>
                            </div>
                            {edgeTypes.map(type => (
                              <Form.Check
                                key={type}
                                type="checkbox"
                                label={type}
                                value={type}
                                checked={selectedEdgeTypes.includes(type)}
                                onChange={handleEdgeTypeSelect}
                                className="mb-1 text-break"
                              />
                            ))}
                          </Col>
                      </Row>
                      
                      <div className="mt-2 border-top pt-2" style={{ flex: '0 0 auto' }}>
                        <div className="d-flex align-items-center mb-2">
                            <h5 className="mb-0 me-3">Property Conditions</h5>
                            <Form.Select 
                                size="sm" 
                                value={newConditionLabel} 
                                onChange={(e) => setNewConditionLabel(e.target.value)}
                                style={{ maxWidth: '100px' }}
                                className="me-2"
                              >
                                <option value="">Select</option>
                                {selectedNodeLabels.map(l => <option key={l} value={l}>{l}</option>)}
                                {selectedEdgeTypes.map(e => <option key={e} value={e}>{e}</option>)}
                            </Form.Select>
                            <InputGroup size="sm" style={{ maxWidth: '250px' }} className="me-2">
                                <Form.Control
                                  placeholder="Condition (e.g. age > 20)"
                                  value={newConditionValue}
                                  onChange={(e) => setNewConditionValue(e.target.value)}
                                />
                                <Button variant="outline-primary" onClick={handleAddCondition}>Add</Button>
                            </InputGroup>
                             <Button variant="primary" size="sm" onClick={() => setShowConditionsModal(true)}>
                                Show Conditions
                             </Button>
                        </div>
                      </div>
                  </Col>

                  <Col md={4} style={{ height: '100%', overflowY: 'auto', borderLeft: '1px solid #dee2e6' }}>
                    <h5 className="mb-3">Settings</h5>
                    <Form.Group className="mb-2">
                      <Form.Label className="small mb-1">Support</Form.Label>
                      <Form.Control type="number" value={support} onChange={handleSupportChange} size="sm" />
                    </Form.Group>
                    <Form.Group className="mb-2">
                       <Form.Label className="small mb-1">Confidence</Form.Label>
                       <Form.Control type="number" value={confidence} onChange={handleConfidenceChange} size="sm" />
                    </Form.Group>
                    <Form.Group className="mb-2">
                       <Form.Label className="small mb-1">Max Length</Form.Label>
                       <Form.Control type="number" value={maxLength} onChange={handleMaxLengthChange} size="sm" />
                    </Form.Group>
                    <Form.Group className="mb-3">
                       <Form.Label className="small mb-1">Max Combination Size</Form.Label>
                       <Form.Control type="number" value={maxCombinationSize} onChange={handleMaxCombinationSizeChange} size="sm" />
                    </Form.Group>
                  </Col>
                </Row>
                
                <div className="mt-2 pt-2 border-top">
                     <div className="text-center mb-2">
                        <Button variant="primary" onClick={handleRunAnalysis} className="fw-bold px-4">Build Forest</Button>
                     </div>
                </div>
              </Form>
            </Card.Body>
          </Card>
        </Col>
      </Row>
      
      <Row className="mb-4">
        <Col md={12}>
            <Card>
                <Card.Header className="d-flex align-items-center fw-bold">
                    <span className="me-2">Generated Trees</span>
                    {analysisAnchors && (
                      <Button 
                        variant="outline-secondary" 
                        size="sm" 
                        onClick={handleDownloadForest} 
                        title="Download Forest Dictionary"
                        className="me-3"
                      >
                        <i className="bi bi-download"></i>
                      </Button>
                    )}
                    {analysisAnchors && (
                        <div className="d-flex flex-wrap gap-1" style={{ overflowX: 'auto' }}>
                          {Object.entries(analysisAnchors).map(([anchor, count]) => (
                            <OverlayTrigger
                              key={anchor}
                              placement="top"
                              overlay={<Tooltip id={`tooltip-${anchor}`}>Count: {count as any}</Tooltip>}
                            >
                              <Button
                                variant="outline-primary"
                                size="sm"
                                className="px-3 py-1"
                                style={{ fontSize: '1rem' }}
                                onClick={() => handleAnchorClick(anchor, maxLength)}
                              >
                                {anchor}
                              </Button>
                            </OverlayTrigger>
                          ))}
                        </div>
                    )}
                </Card.Header>
            </Card>
        </Col>
      </Row>

      <Row>
        <Col md={6} className="d-flex align-items-stretch">
          <Card className="w-100 h-100">
            <Card.Header className="d-flex justify-content-between align-items-center fw-bold">
               <span>Frequent Sequences</span>
               {sequences && Object.keys(sequences).length > 0 && (
                   <Button variant="outline-secondary" size="sm" onClick={() => handleDownloadCSV('patterns/csv', 'frequent_patterns.csv')} title="Download Patterns CSV">
                       <i className="bi bi-file-earmark-spreadsheet"></i>
                   </Button>
               )}
            </Card.Header>
            <Card.Body>
              {sequences && !isSearching && Object.keys(sequences).length > 0 && (
                <div className="mt-3">
                  <SequenceList
                    patterns={sequences}
                    anchorLabel={(Object.values(sequences)[0] as Sequence).anchor}
                    onSelectedPatternsChange={handleSelectedSequencesChange}
                    onCombinePatterns={handleCombineSequences}
                  />
                </div>
              )}
              {sequences && !isSearching && Object.keys(sequences).length === 0 && (
                <div className="mt-3">
                  <p>No sequences found for the selected anchor and length.</p>
                </div>
              )}
            </Card.Body>
          </Card>
        </Col>
        <Col md={6} className="d-flex align-items-stretch">
          <Card className="w-100 h-100">
            <Card.Header className="d-flex justify-content-between align-items-center fw-bold">
               <span>Combination Visualization</span>
               {combinedResults && (
                   <Button variant="outline-secondary" size="sm" onClick={() => handleDownloadCSV('combinations/csv', 'frequent_combinations.csv')} title="Download Combinations CSV">
                       <i className="bi bi-file-earmark-spreadsheet"></i>
                   </Button>
               )}
            </Card.Header>
            <Card.Body>
              {isCombining && <p>Combining sequences...</p>}
              {combinedResults && !isCombining && (
                <div className="mt-3">
                  <CombinationResults
                    combinationData={combinedResults}
                    onSelectCombination={handleSelectCombination}
                  />
                </div>
              )}
            </Card.Body>
          </Card>
        </Col>
      </Row>

      {rules && !isFetchingRules && (
        <Row className="mt-4">
          <Col md={12}>
            <Card>
              <Card.Header className="d-flex justify-content-between align-items-center fw-bold">
                <span>Rule Visualization</span>
                 <Button variant="outline-secondary" size="sm" onClick={() => handleDownloadCSV('rules/csv', 'association_rules.csv')} title="Download Rules CSV">
                      <i className="bi bi-file-earmark-spreadsheet"></i>
                 </Button>
              </Card.Header>
              <Card.Body>
                {isFetchingRules && <p>Generating rules...</p>}
                <Row>
                  <Col md={6}>
                    <RuleStructureList
                      rules={rules}
                      onShowInstances={handleShowRuleInstances}
                      fidToPidMap={fidToPidMap}
                      patterns={sequences}
                    />
                  </Col>
                  <Col md={6}>
                    {rulesToShow.length > 0 && (
                      <RuleDetails
                        rules={rulesToShow}
                        fidToPidMap={fidToPidMap}
                        patterns={sequences}
                      />
                    )}
                  </Col>
                </Row>
              </Card.Body>
            </Card>
          </Col>
        </Row>
      )}

      {/* Loading Modal for Sequence Search */}
      <Modal show={isSearching} centered backdrop="static" keyboard={false}>
        <Modal.Header className="justify-content-center">
          <Modal.Title className="w-100 text-center fw-bold">
            {searchStatus === 'searching' ? 'Sequence Search' : searchStatus === 'success' ? 'Search Complete' : 'Search Error'}
          </Modal.Title>
        </Modal.Header>
        <Modal.Body className="text-center py-4">
          {searchStatus === 'searching' && (
            <>
              <Spinner animation="border" variant="primary" className="mb-3" />
              <p className="mb-0">Searching for sequences, please wait...</p>
              <small className="text-muted">This might take a while depending on the data size.</small>
            </>
          )}
          {searchStatus === 'success' && (
            <>
              <div className="mb-3">
                <i className="bi bi-check-circle-fill text-success" style={{ fontSize: '3rem' }}></i>
              </div>
              <p className="mb-0 fw-bold">Sequences successfully retrieved!</p>
            </>
          )}
          {searchStatus === 'error' && (
            <>
              <div className="mb-3">
                <i className="bi bi-x-circle-fill text-danger" style={{ fontSize: '3rem' }}></i>
              </div>
              <p className="mb-0 fw-bold">An error occurred while searching for sequences.</p>
              <small className="text-muted">Please check the console for details.</small>
            </>
          )}
        </Modal.Body>
      </Modal>

      <Modal show={showConditionsModal} onHide={() => setShowConditionsModal(false)} centered>
        <Modal.Header closeButton>
            <Modal.Title>Active Property Conditions</Modal.Title>
        </Modal.Header>
        <Modal.Body>
            {Object.keys(conditions).length === 0 ? (
                <p className="text-muted text-center my-3">No conditions set.</p>
            ) : (
                Object.entries(conditions).map(([label, values]) => (
                    <div key={label} className="mb-3">
                        <strong className="d-block mb-1">{label}:</strong>
                        <div className="d-flex flex-wrap gap-2">
                            {values.map((val, idx) => (
                                <span key={idx} className="badge bg-light text-dark border d-flex align-items-center p-2">
                                    <span className="me-2">{val}</span>
                                    <i 
                                        className="bi bi-x-circle-fill text-danger cursor-pointer" 
                                        style={{ fontSize: '1rem' }} 
                                        onClick={() => handleRemoveCondition(label, val)}
                                        title="Remove condition"
                                    ></i>
                                </span>
                            ))}
                        </div>
                    </div>
                ))
            )}
        </Modal.Body>
        <Modal.Footer>
            <Button variant="secondary" onClick={() => setShowConditionsModal(false)}>
                Close
            </Button>
        </Modal.Footer>
      </Modal>
    </Container>
  );
}

export default App;