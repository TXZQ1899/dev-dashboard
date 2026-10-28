import { Shell } from '../page';
import DeploymentUnits from '../applications/deployment-units';

export default function DeploymentUnitsPage() {
  return (
    <Shell active="deployment-units">
      <main>
        <DeploymentUnits />
      </main>
    </Shell>
  );
}
