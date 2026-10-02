import { Link } from "react-router-dom";

import { SiteLayout } from "@/components/site/SiteLayout";

const NotFound = () => (
  <SiteLayout>
    <div className="mx-auto flex max-w-xl flex-col items-center px-4 py-32 text-center">
      <p className="mono text-sm text-muted-foreground">404</p>
      <h1 className="mt-2 text-3xl font-bold tracking-tight">Page not found</h1>
      <p className="mt-3 text-muted-foreground">That route doesn't exist on Rev9 Apis.</p>
      <Link to="/" className="btn-primary mt-8">
        Back to home
      </Link>
    </div>
  </SiteLayout>
);

export default NotFound;
