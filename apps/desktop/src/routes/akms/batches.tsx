/**
 * AKMS Batches route.
 *
 * Hosts the new AKMS Batches page: list (left) + detail with sub-tabs
 * (right) + Run-workflow dialog. The legacy paper-picker UI lives at
 * /akms/picker for source curation.
 */
import { BatchesPage } from "../../components/batches/BatchesPage";

export default function AkmsBatchesRoute() {
  return <BatchesPage />;
}
