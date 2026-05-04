# Documentation Index - BA/Product Guidelines
## 📚 Core Documents

### Repository Navigation
- **[PROJECT_GUIDE.md](PROJECT_GUIDE.md)** - Repository map and role-based entry points
- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - Common commands, endpoints, and troubleshooting
- **[PROJECT_STATUS.md](PROJECT_STATUS.md)** - Summary of completed work
- **[AUTOMATION.md](AUTOMATION.md)** - Automated pipeline startup and monitoring notes

### 1. **[REQUIREMENTS.md](REQUIREMENTS.md)** - Product Requirements
- Business objectives & success metrics
- Functional requirements (F1-F19)
- Non-functional requirements (Performance, Reliability, Data Quality)
- Data requirements & constraints
- Out of scope items

**Use this for:** Understanding what the system should do, defining success.

---

### 2. **[DATA_SCHEMA.md](DATA_SCHEMA.md)** - Data Model & Validation
- Kafka topic schemas with examples
- MongoDB collection structures
- Model input features (numeric, categorical, text)
- Model candidate criteria
- Data quality rules & outlier detection
- Data lineage & versioning strategy
- Encoding standards

**Use this for:** Understanding data flow, implementing validation, schema design.

---

### 3. **[METRICS_AND_SLA.md](METRICS_AND_SLA.md)** - KPIs & Service Levels
- Business KPIs (R² score, MAE, model health)
- Operational metrics (throughput, latency, resource utilization)
- Data quality metrics (completeness, accuracy, timeliness)
- Service level agreements (SLA) by component
- Error rate targets
- Cost metrics
- Dashboard queries

**Use this for:** Defining success criteria, setting targets, tracking performance.

---

### 4. **[ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md)** - Quality Gates
- Acceptance criteria by stage (Scraper, Normalization, Training, Prediction)
- Quality gates 1-6 (checkpoints in pipeline)
- Data quality scoring formula
- Acceptance report template
- Stakeholder sign-off checklist

**Use this for:** QA testing, release approval, quality assurance.

---

### 5. **[ERROR_HANDLING_STRATEGY.md](ERROR_HANDLING_STRATEGY.md)** - Error Management
- Error classification (by severity & recovery time)
- Error handling for each component (Scraper, Kafka, Database, Model, Prediction)
- Recovery strategies & retry logic
- Error codes mapping (001-1199)
- Alerting rules (CRITICAL, HIGH, MEDIUM, LOW)
- Post-incident procedures
- Error budget allocation

**Use this for:** Incident response, debugging, recovery planning.

---

### 6. **[MONITORING_AND_ALERTING.md](MONITORING_AND_ALERTING.md)** - Observability
- Monitoring architecture
- Key metrics by component (scraper, Kafka, MongoDB, model, pipeline)
- Alerting rules with conditions & runbooks
- Dashboard specifications
- Structured logging standards
- Runbook templates
- On-call rotation procedures
- Daily/weekly/monthly checklists

**Use this for:** Setting up monitoring, defining alerts, on-call procedures.

---

### 7. **[FLOW_DIAGRAMS.md](FLOW_DIAGRAMS.md)** - Architecture & Data Flow
- Overall pipeline architecture
- Detailed data flows with decision points:
  - Scraper → Kafka flow
  - Kafka → MongoDB flow
  - Model training flow
  - Prediction flow
  - Error handling flow
  - Quality gates flow

**Use this for:** Understanding system design, documentation, design reviews.

---

### 8. **[FLOW_ANALYSIS.md](FLOW_ANALYSIS.md)** - Current State Assessment
- Current flow assessment (what's good, what needs fixing)
- 8 critical issues with:
  - Current problem description
  - Why it matters
  - Recommended fix with code example
- Priority fixes for BA role (Tier 1, 2, 3)
- Recommended actions for next sprint

**Use this for:** Understanding project status, planning fixes, sprint planning.

---

## 🚀 Quick Start by Role

### For Product Managers / Business Analysts
1. Start with **REQUIREMENTS.md** - Understand what we're building
2. Read **METRICS_AND_SLA.md** - Learn success metrics
3. Review **ACCEPTANCE_CRITERIA.md** - Quality standards
4. Check **FLOW_ANALYSIS.md** - Current status & gaps

**Purpose:** Understand business requirements, define success, plan roadmap.

---

### For Data Engineers
1. Start with **DATA_SCHEMA.md** - Learn data model
2. Read **FLOW_DIAGRAMS.md** - Understand architecture
3. Check **ERROR_HANDLING_STRATEGY.md** - Error codes & recovery
4. Review **FLOW_ANALYSIS.md** - Specific fixes needed

**Purpose:** Implement correct data pipelines, handle errors, ensure quality.

---

### For ML Engineers
1. Start with **DATA_SCHEMA.md** - Input features & format
2. Read **METRICS_AND_SLA.md** - Model performance targets
3. Check **ACCEPTANCE_CRITERIA.md** - Model quality gates
4. Review **MONITORING_AND_ALERTING.md** - Model metrics to track

**Purpose:** Build performant models, monitor drift, meet SLAs.

---

### For DevOps / Site Reliability Engineers
1. Start with **MONITORING_AND_ALERTING.md** - What to monitor
2. Read **ERROR_HANDLING_STRATEGY.md** - Error handling & recovery
3. Check **FLOW_DIAGRAMS.md** - System architecture
4. Review **FLOW_ANALYSIS.md** - Current issues

**Purpose:** Setup monitoring, alerting, on-call procedures, incident response.

---

### For QA / Testing Team
1. Start with **ACCEPTANCE_CRITERIA.md** - Quality standards
2. Read **METRICS_AND_SLA.md** - Performance targets
3. Check **DATA_SCHEMA.md** - Valid data formats
4. Review **ERROR_HANDLING_STRATEGY.md** - Error scenarios

**Purpose:** Design test cases, validate acceptance criteria, report bugs.

---

## 📊 Documentation by Topic

### Business Requirements
- REQUIREMENTS.md (overall vision)
- METRICS_AND_SLA.md (success metrics)
- FLOW_ANALYSIS.md (current status)

### Data & Schema
- DATA_SCHEMA.md (complete reference)
- FLOW_DIAGRAMS.md (data lineage)
- FLOW_ANALYSIS.md (current implementation)

### Quality & Acceptance
- ACCEPTANCE_CRITERIA.md (quality gates)
- METRICS_AND_SLA.md (performance targets)
- MONITORING_AND_ALERTING.md (quality tracking)

### Operations & Reliability
- ERROR_HANDLING_STRATEGY.md (failure modes & recovery)
- MONITORING_AND_ALERTING.md (observability & alerts)
- FLOW_ANALYSIS.md (issues & fixes)

### System Design
- FLOW_DIAGRAMS.md (architecture & flows)
- DATA_SCHEMA.md (data models)
- REQUIREMENTS.md (constraints)

---

## 📋 Key Metrics at a Glance

| Metric | Target | Status |
|--------|--------|--------|
| **Model Quality** |
| R² Score | > 0.75 | ? (TBD) |
| MAE | < 500M VND | ? (TBD) |
| RMSE | < 800M VND | ? (TBD) |
| **Pipeline Health** |
| Availability | 99.5% | ? (TBD) |
| Error Rate | < 0.5% | ? (TBD) |
| Data Freshness | < 1 hour | ? (TBD) |
| **Data Quality** |
| Completeness | > 90% | ? (TBD) |
| Candidate Rate | > 60% | ? (TBD) |
| Duplicate Rate | < 0.5% | ? (TBD) |
| **Performance** |
| Scrape latency | 5 sec | ? (TBD) |
| Pipeline latency | < 30 min | ? (TBD) |
| Prediction latency | < 500 ms | ? (TBD) |

---

## 🔗 Document Relationships

```
REQUIREMENTS.md (what)
├─► METRICS_AND_SLA.md (how to measure success)
├─► DATA_SCHEMA.md (what data needed)
│   └─► FLOW_DIAGRAMS.md (how data flows)
├─► ACCEPTANCE_CRITERIA.md (how to verify)
│   └─► MONITORING_AND_ALERTING.md (how to track)
└─► ERROR_HANDLING_STRATEGY.md (what if it fails)
    └─► FLOW_ANALYSIS.md (what's broken & how to fix)
```

---

## 📝 Next Steps

### For This Sprint:
1. ✅ Read all documents (1-2 hours)
2. ✅ Identify gaps & questions
3. ⚠️ Review FLOW_ANALYSIS.md for specific fixes needed
4. ⚠️ Plan Tier 1 & Tier 2 fixes

### For Product Team:
- [ ] Approve REQUIREMENTS.md
- [ ] Agree on METRICS_AND_SLA.md targets
- [ ] Sign off on ACCEPTANCE_CRITERIA.md

### For Engineering Team:
- [ ] Review DATA_SCHEMA.md for implementation
- [ ] Plan fixes from FLOW_ANALYSIS.md
- [ ] Setup monitoring from MONITORING_AND_ALERTING.md

### For QA Team:
- [ ] Create test cases from ACCEPTANCE_CRITERIA.md
- [ ] Setup test environments
- [ ] Plan test execution

---

## ❓ FAQ

**Q: Where do I find the definition of a data field?**
A: Check DATA_SCHEMA.md - all fields documented with type, range, validation.

**Q: What metrics should I track?**
A: See METRICS_AND_SLA.md - has all KPIs and monitoring requirements.

**Q: How do we know if system is working correctly?**
A: Check ACCEPTANCE_CRITERIA.md - quality gates defined at each stage.

**Q: What if something breaks?**
A: See ERROR_HANDLING_STRATEGY.md - error codes, recovery steps, alerting rules.

**Q: How is data flowing through the system?**
A: See FLOW_DIAGRAMS.md - complete architecture with decision points.

**Q: What's the current status and what needs to be fixed?**
A: See FLOW_ANALYSIS.md - assessment of current state and priority fixes.

---

## 📞 Document Owner

**Owner:** Data Engineering Lead + Product Manager  
**Last Updated:** 2024-04-29  
**Status:** DRAFT (Ready for review)  
**Next Review:** After first code fixes implemented

---

## 📖 Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2024-04-29 | Initial documentation for BA review |
| 1.1 | TBD | After stakeholder feedback |
| 2.0 | TBD | After Tier 1 fixes implemented |
