import React, { useState, useRef, useEffect } from 'react';
import { Button } from 'react-bootstrap';
import './PatternList.css';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || 'http://localhost:8081';

interface Pattern {
  id: string;
  kind: string;
  anchor: string;
  node_labels: string[];
  rel_types: (string | null)[];
  count?: number;
}

interface PatternListProps {
  patterns: { [key: string]: Pattern };
  anchorLabel: string;
  onSelectedPatternsChange: (patternIds: string[]) => void;
  onCombinePatterns: () => void;
}

interface NodeInstance {
  name: string;
}

const PatternItem: React.FC<{
  pattern: Pattern;
  isSelected: boolean;
  onSelect: (id: string) => void;
  isHovered: boolean;
  isFetchingInstances: boolean;
  hoveredInstances: NodeInstance[];
  scale: number;
  onReportRatio: (id: string, ratio: number | undefined) => void;
  children: React.ReactNode;
}> = ({ pattern, isSelected, onSelect, isHovered, isFetchingInstances, hoveredInstances, scale, onReportRatio, children }) => {
  const rowRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleResize = () => {
      if (rowRef.current && contentRef.current) {
        const containerWidth = rowRef.current.clientWidth;
        const contentWidth = contentRef.current.scrollWidth;

        // Pattern list area has padding, so we might need to adjust if rowRef includes it?
        // rowRef is the .pattern-row div. It is a block (flex) element.
        // Its clientWidth is the available width.
        
        // We use a safe margin to avoid edge clipping
        const availableWidth = containerWidth; 

        if (contentWidth > 0) {
            const ratio = availableWidth / contentWidth;
            onReportRatio(pattern.id, ratio);
        }
      }
    };

    // Calculate initially and on resize
    handleResize();
    window.addEventListener('resize', handleResize);
    
    // Also recalculate after a short delay to ensure rendering is complete
    const timeoutId = setTimeout(handleResize, 0);

    return () => {
        window.removeEventListener('resize', handleResize);
        clearTimeout(timeoutId);
        onReportRatio(pattern.id, undefined);
    };
  }, [children, pattern.id, onReportRatio]); // Recalculate if children change

  return (
    <div
      className={`pattern-row ${isSelected ? 'selected' : ''}`}
      ref={rowRef}
      style={{ width: '100%' }} 
    >
      <div 
        className="pattern-content-wrapper"
        style={{ 
            width: '100%',
            // If we scale down, we want to align left.
            display: 'flex',
            justifyContent: 'flex-start',
            overflow: 'hidden'
        }}
      >
          <div 
            className="pattern-content"
            onClick={() => onSelect(pattern.id)}
            ref={contentRef}
            style={{
                transform: `scale(${scale})`,
                transformOrigin: 'left center',
                // Important: fit-content ensures the div is only as wide as the nodes/rels
                // allowing us to measure the true width vs the container width.
                width: 'max-content', 
                minWidth: 'max-content' 
            }}
          >
            {children}
          </div>
      </div>
      
      {isHovered && (
        <div className="pattern-instances-tooltip">
          {isFetchingInstances ? (
            <p>Loading instances...</p>
          ) : hoveredInstances.length > 0 ? (
            <ul>
              {hoveredInstances.map((instance, i) => (
                <li key={i}>{instance.name}</li>
              ))}
            </ul>
          ) : (
            <p>No instances found.</p>
          )}
        </div>
      )}
    </div>
  );
};

const PatternList: React.FC<PatternListProps> = ({ patterns, anchorLabel, onSelectedPatternsChange, onCombinePatterns }) => {
  const [hoveredPatternId, setHoveredPatternId] = useState<string | null>(null);
  const [selectedPatternIds, setSelectedPatternIds] = useState<string[]>([]);
  const [hoveredPatternInstances, setHoveredPatternInstances] = useState<NodeInstance[]>([]);
  const [isFetchingInstances, setIsFetchingInstances] = useState(false);
  const listAreaRef = useRef<HTMLDivElement>(null);

  // Global scaling logic
  const [globalScale, setGlobalScale] = useState(1);
  const ratiosRef = useRef<{ [key: string]: number }>({});

  const handleReportRatio = React.useCallback((id: string, ratio: number | undefined) => {
      if (ratio === undefined) {
          delete ratiosRef.current[id];
      } else {
          // If ratio is practically same, ignore to avoid re-calcs
          if (Math.abs((ratiosRef.current[id] || 0) - ratio) < 0.001) return;
          ratiosRef.current[id] = ratio;
      }

      const allRatios = Object.values(ratiosRef.current);
      let minRatio = 1;
      if (allRatios.length > 0) {
          // Find minimum ratio, capped at 1 (we only scale down)
          minRatio = Math.min(1, ...allRatios);
      }
      
      setGlobalScale(prev => {
          if (Math.abs(prev - minRatio) > 0.001) {
              return minRatio;
          }
          return prev;
      });
  }, []);

  const filteredPatterns = Object.values(patterns).filter(s => s.anchor === anchorLabel);

  useEffect(() => {
    if (listAreaRef.current) {
        const listArea = listAreaRef.current;
        // We use a timeout to allow the DOM to update (especially with scaling potentially affecting layout, 
        // although transform shouldn't affect offsetTop/Height of the row wrapper)
        setTimeout(() => {
            const lastPatternRow = listArea.querySelector('.pattern-row:last-child');

            if (lastPatternRow) {
                const rowTop = (lastPatternRow as HTMLElement).offsetTop;
                const rowHeight = lastPatternRow.clientHeight;
                const verticalLineHeight = 20 + rowTop + (rowHeight / 2);
                listArea.style.setProperty('--vertical-line-height', `${verticalLineHeight}px`);
            } else {
                listArea.style.setProperty('--vertical-line-height', '20px');
            }
        }, 50);
    }
  }, [filteredPatterns]);

  const handlePatternClick = (patternId: string) => {
    const newSelectedPatternIds = selectedPatternIds.includes(patternId)
      ? selectedPatternIds.filter(id => id !== patternId)
      : [...selectedPatternIds, patternId];
    setSelectedPatternIds(newSelectedPatternIds);
    onSelectedPatternsChange(newSelectedPatternIds);
  };

  const handlePatternHover = async (patternId: string | null) => {
    setHoveredPatternId(patternId);
    if (patternId) {
      setIsFetchingInstances(true);
      setHoveredPatternInstances([]);
      try {
        const response = await fetch(`${BACKEND_URL}/api/patterns/${patternId}/instances`);
        const data = await response.json();
        setHoveredPatternInstances(data.instances);
      } catch (error) {
        console.error('Error fetching pattern instances:', error);
      } finally {
        setIsFetchingInstances(false);
      }
    } else {
      setHoveredPatternInstances([]);
    }
  };

  const getPatternStructure = (pattern: Pattern) => {
    const structure: React.ReactNode[] = [];
    if (pattern.kind === 'anyrel') {
      const relType = `*ANYREL*${pattern.rel_types.length > 0 ? `:${pattern.rel_types.length}` : ''}`;
      structure.push(
        <div className="pattern-rel first-rel" key="rel-any">
          <div className="pattern-rel-label">{relType}</div>
        </div>
      );
      structure.push(
        <span 
          className="pattern-node final" 
          key="node-0"
          onMouseEnter={() => handlePatternHover(pattern.id)}
          onMouseLeave={() => handlePatternHover(null)}
        >
          {pattern.node_labels[0]}
        </span>
      );
    } else {
      for (let i = 0; i < pattern.rel_types.length; i++) {
        const relType = pattern.rel_types[i] || 'ANYREL';
        const targetNodeLabel = pattern.node_labels[i + 1];
        const isFinalNode = i === pattern.rel_types.length - 1;
        const nodeClassName = `pattern-node ${isFinalNode ? 'final' : 'intermediate'}`;
        
        let relLabel = relType;
        if (pattern.kind === 'count' && pattern.count !== undefined) {
            relLabel = `${relType} (x${pattern.count})`;
        }
        
        const isFirstRel = i === 0;
        const relClassName = `pattern-rel ${isFirstRel ? 'first-rel' : ''}`;

        structure.push(
          <div className={relClassName} key={`rel-${i}`}>
            <div className="pattern-rel-label">{relLabel}</div>
          </div>
        );

        if (isFinalNode) {
          structure.push(
            <span 
              className={nodeClassName} 
              key={`node-${i}`}
              onMouseEnter={() => handlePatternHover(pattern.id)}
              onMouseLeave={() => handlePatternHover(null)}
            >
              {targetNodeLabel}
            </span>
          );
        } else {
          structure.push(<span className={nodeClassName} key={`node-${i}`}>{targetNodeLabel}</span>);
        }
      }
    }
    return structure;
  };

  return (
    <div className="pattern-visualization-container">
        <div className="pattern-header-and-controls">
            <div className="anchor-label-display">
                <div className="anchor-box">{anchorLabel}</div>
            </div>
            <div className="pattern-controls">
                {selectedPatternIds.length > 0 && (
                    <Button variant="primary" onClick={onCombinePatterns} className="combine-patterns-btn">
                        Combine Sequences
                    </Button>
                )}
            </div>
        </div>

        <div className="patterns-list-area" ref={listAreaRef}>
            {filteredPatterns.length === 0 ? (
                <p>No patterns found for this anchor.</p>
            ) : (
                filteredPatterns.map(pattern => (
                  <PatternItem
                    key={pattern.id}
                    pattern={pattern}
                    isSelected={selectedPatternIds.includes(pattern.id)}
                    onSelect={handlePatternClick}
                    isHovered={hoveredPatternId === pattern.id}
                    isFetchingInstances={isFetchingInstances}
                    hoveredInstances={hoveredPatternInstances}
                    scale={globalScale}
                    onReportRatio={handleReportRatio}
                  >
                    {getPatternStructure(pattern)}
                  </PatternItem>
                ))
            )}
        </div>
    </div>
  );
};

export default PatternList;