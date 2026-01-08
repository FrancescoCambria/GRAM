import React from 'react';
import './CombinationResults.css';

interface CombinationGroup {
  structure: { [key: string]: number };
  count: number;
  combinations: any[];
}

interface CombinationResultsProps {
  combinationData: CombinationGroup[];
  onSelectCombination: (combinationGroup: CombinationGroup) => void;
}

const CombinationResults: React.FC<CombinationResultsProps> = ({ combinationData, onSelectCombination }) => {
  return (
    <div className="combination-results-container">
      {combinationData.map((group, index) => (
        <button 
          key={index} 
          className="combination-group combination-group-button"
          onClick={() => onSelectCombination(group)}
        >
          <h5 className="combination-structure-title">
            Combination Type {index + 1} <span className="total-instances-display"><b>Total Instances:</b> {group.count}</span>
          </h5>
          <div className="combination-structure">
            {Object.entries(group.structure).map(([struct, count]) => (
              <div key={struct} className="structure-item">
                <span className="structure-count">{count}x</span>
                <span className="structure-name">{struct}</span>
              </div>
            ))}
          </div>
        </button>
      ))}
    </div>
  );
};

export default CombinationResults;
