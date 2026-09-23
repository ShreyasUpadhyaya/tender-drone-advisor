const services = [
  "Web workspace",
  "FastAPI health service",
  "PostgreSQL + pgvector",
  "Redis",
  "MinIO",
];

export default function HomePage() {
  return (
    <main>
      <p className="eyebrow">Tender Drone Advisor</p>
      <h1>Platform foundation is ready.</h1>
      <p className="summary">
        C01 provides local service topology and a typed health endpoint. Tender
        processing, catalog logic, recommendations, and model integrations begin
        only in approved later commits.
      </p>
      <section aria-labelledby="foundation-services">
        <h2 id="foundation-services">Local development services</h2>
        <ul>
          {services.map((service) => (
            <li key={service}>{service}</li>
          ))}
        </ul>
      </section>
    </main>
  );
}
