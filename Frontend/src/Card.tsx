import Button from 'react-bootstrap/Button';
import Card from 'react-bootstrap/Card';

interface TsxCardProps {
  text: string;
}

function TsxCard({ text }: TsxCardProps) {
  return (
    <Card style={{ width: '60%', backgroundColor: 'white', padding: '25px', borderRadius: '10px'}}>
      <Card.Body>
        <Card.Title>{text}</Card.Title>
        <Card.Text>
          Some quick example text to build on the card title and make up the
          bulk of the card's content.
        </Card.Text>
        <Button variant="primary">Go somewhere</Button>
      </Card.Body>
    </Card>
  );
}

export default TsxCard;
