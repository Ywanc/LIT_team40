import { useState } from 'react'
import './App.css'
import Card from 'react-bootstrap/Card';
import TextareaAutosize from 'react-textarea-autosize';
import Spinner from 'react-bootstrap/Spinner';

function AutoResizingBox() {
  const [value, setValue] = useState('');

  return (
    <div>
      <TextareaAutosize
        id="autofit-input"
        minRows={15}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Enter text from AI..."
        style={{
          width: '100%',
          padding: '10px',
          boxSizing: 'border-box',
          fontSize: '16px',
          resize: 'none',
          overflow: 'hidden'
        }}
      />
    </div>
  );
}

function App() {
  const [openCase, setOpenCase] = useState(0);
  const [showGrid, setShowGrid] = useState(false);
  const [loading, setLoading] = useState(false);

  const cases = [
    {
      name: "Case 1",
      color: "green",
    },
    {
      name: "Case 2",
      color: "red",
    },
    {
      name: "Case 3",
      color: "empty",
    },
  ];

  const toggleCase = (caseNumber) => {
    setOpenCase(openCase === caseNumber ? null : caseNumber);
  };

  const handleSearch = async () => {
    setLoading(true);

    await new Promise(resolve => setTimeout(resolve, 2000));

    setLoading(false);
    setShowGrid(true);
  };

  return (
    <>
      <div style={{
        backgroundColor: '#f0f0f0',
        minHeight: '100vh'
      }}>
        <section id="center">
          <div>
            <h1>SiteCheck</h1>
            <p>
              Don't just generate. Verify.
            </p>
          </div>

          <div className="card">
            <AutoResizingBox></AutoResizingBox>
            <button
              type="button"
              className="counter"
              onClick={handleSearch}
              disabled={loading}
            >
              {loading ? "Searching..." : "Search"}
            </button>
          </div>
        </section>

        <br></br>

        <section>
          {showGrid && (
            <div className="card">

              <h1>Analysis</h1>

              {/* Header */}
              <div className="analysis-grid header">
                <div className="case-column"></div>

                <div>Case Name</div>
                <div>Citation</div>
                <div>Court</div>
                <div>Year</div>
                <div>Judge</div>
                <div>Jurisdiction</div>
                <div>State of Case</div>
              </div>

              {/* Cases */}
              {cases.map((item, index) => {
                const caseNumber = index + 1;
                const isOpen = openCase === caseNumber;

                return (
                  <div key={item.name}>

                    <div className="analysis-grid case-row">

                      {/* Case name */}
                      <button
                        className="case-name"
                        onClick={() => toggleCase(caseNumber)}
                      >
                        {item.name}

                        <span className={`arrow ${isOpen ? "up" : ""}`}>
                          {isOpen ? "⌃" : "⌄"}
                        </span>
                      </button>

                      {/* Data cells */}
                      <div className={`cell ${item.color}`}></div>
                      <div className={`cell ${item.color}`}></div>
                      <div className={`cell ${item.color}`}></div>
                      <div className={`cell ${item.color}`}></div>
                      <div className={`cell ${item.color}`}></div>
                      <div className={`cell ${item.color}`}></div>
                      <div className={`cell ${item.color}`}></div>

                    </div>

                    {/* Dropdown */}
                    {isOpen && (
                      <div className="dropdown">
                        #dropdown ok
                      </div>
                    )}

                  </div>
                );
              })}

            </div>
          )}
        </section>
      </div>
    </>
  )
}

export default App
