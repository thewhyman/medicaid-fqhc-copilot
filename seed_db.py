"""Create and seed the patients PostgreSQL database with mock data."""

import json
from datetime import date, timedelta

import psycopg2

from config import DATABASE_URL


def create_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS patients (
                id SERIAL PRIMARY KEY,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                date_of_birth TEXT NOT NULL,
                age INTEGER NOT NULL,
                state TEXT NOT NULL,
                county TEXT,
                household_size INTEGER NOT NULL,
                annual_income REAL NOT NULL,
                income_source TEXT,
                is_pregnant BOOLEAN NOT NULL DEFAULT FALSE,
                has_disability BOOLEAN NOT NULL DEFAULT FALSE,
                is_us_citizen BOOLEAN NOT NULL DEFAULT TRUE,
                immigration_status TEXT DEFAULT 'citizen',
                current_insurance TEXT,
                consent_status VARCHAR(10) DEFAULT 'pending',
                preferred_language VARCHAR(5) DEFAULT 'en',
                response_history JSONB DEFAULT '[]',
                prior_doc_issues JSONB DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS conversations (
                session_id TEXT PRIMARY KEY,
                patient_id INTEGER,
                messages JSONB NOT NULL DEFAULT '[]',
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                FOREIGN KEY (patient_id) REFERENCES patients(id)
            );

            CREATE TABLE IF NOT EXISTS eligibility_history (
                id SERIAL PRIMARY KEY,
                patient_id INTEGER NOT NULL,
                determination_date TEXT NOT NULL,
                eligible BOOLEAN NOT NULL,
                category TEXT,
                reasoning TEXT,
                FOREIGN KEY (patient_id) REFERENCES patients(id)
            );

            CREATE TABLE IF NOT EXISTS renewals (
                id SERIAL PRIMARY KEY,
                patient_id INTEGER REFERENCES patients(id),
                renewal_due_date DATE NOT NULL,
                current_step VARCHAR(20) DEFAULT 'IDENTIFIED',
                risk_score DECIMAL(3,2),
                risk_tier VARCHAR(10),
                risk_factors JSONB DEFAULT '[]',
                documents_required JSONB DEFAULT '[]',
                documents_received JSONB DEFAULT '[]',
                communication_log JSONB DEFAULT '[]',
                consent_status VARCHAR(10) DEFAULT 'pending',
                preferred_language VARCHAR(5) DEFAULT 'en',
                assigned_caseworker VARCHAR(100),
                previous_renewal_outcome VARCHAR(20),
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                completed_at TIMESTAMP,
                outcome VARCHAR(20)
            );

            CREATE TABLE IF NOT EXISTS documents (
                id SERIAL PRIMARY KEY,
                renewal_id INTEGER REFERENCES renewals(id),
                document_type VARCHAR(50),
                file_path VARCHAR(255),
                upload_timestamp TIMESTAMP DEFAULT NOW(),
                status VARCHAR(20) DEFAULT 'pending',
                extracted_data JSONB,
                confidence DECIMAL(3,2),
                reviewed_by VARCHAR(100),
                review_notes TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT NOW(),
                patient_id INTEGER,
                renewal_id INTEGER,
                actor VARCHAR(100),
                action VARCHAR(100),
                details JSONB,
                phi_accessed BOOLEAN DEFAULT FALSE
            );
        """)
    conn.commit()


def seed_patients(conn):
    patients = [
        ("Maria", "Garcia", "1998-03-15", 28, "CA", "Los Angeles", 3, 18000.0,
         "employment", True, False, True, "citizen", None,
         "opted_in", "en", "[]", "[]"),
        ("James", "Wilson", "1981-07-22", 45, "TX", "Harris", 1, 14000.0,
         "employment", False, False, True, "citizen", None,
         "opted_in", "en", "[]", "[]"),
        ("Sarah", "Johnson", "2019-01-10", 7, "FL", "Miami-Dade", 4, 35000.0,
         "employment", False, False, True, "citizen", None,
         "opted_in", "en", "[]", "[]"),
        ("Robert", "Chen", "1956-11-03", 70, "NY", "Kings", 2, 22000.0,
         "social_security", False, False, True, "citizen", "medicare",
         "opted_in", "en", "[]", "[]"),
        ("Aisha", "Patel", "1994-05-28", 32, "OH", "Franklin", 5, 42000.0,
         "employment", False, False, True, "citizen", None,
         "opted_in", "en", "[]", "[]"),
        ("David", "Thompson", "1971-09-14", 55, "GA", "Fulton", 1, 8000.0,
         "social_security", False, True, True, "citizen", None,
         "opted_in", "en", "[]", "[]"),
        ("Lisa", "Martinez", "2007-06-20", 19, "WA", "King", 2, 25000.0,
         "employment", False, False, True, "citizen", None,
         "opted_in", "en", "[]", "[]"),
        ("Michael", "Brown", "1986-12-01", 40, "AL", "Jefferson", 3, 12000.0,
         "self-employment", False, False, True, "citizen", None,
         "opted_in", "en", "[]", "[]"),
        # --- Border / edge cases ---
        # #9: Income exactly at threshold (CA adult, 138% of $15,650 = $21,597)
        ("Elena", "Ruiz", "1990-04-10", 36, "CA", "San Diego", 1, 21597.0,
         "employment", False, False, True, "citizen", None,
         "opted_in", "en", "[]", "[]"),
        # #10: Income $1 over threshold (OH adult, 138% of $15,650 = $21,597 → $21,598)
        ("Kevin", "Park", "1988-11-25", 38, "OH", "Cuyahoga", 1, 21598.0,
         "employment", False, False, True, "citizen", None,
         "opted_in", "en", "[]", "[]"),
        # #11: Non-US citizen — should be ineligible regardless of income
        ("Yuki", "Tanaka", "1995-02-14", 31, "NY", "Queens", 2, 10000.0,
         "employment", False, False, False, "non-immigrant", None,
         "opted_in", "en", "[]", "[]"),
        # #12: Age 18 — just crossed from child to adult (higher threshold lost)
        ("Jordan", "Lee", "2008-01-05", 18, "FL", "Broward", 3, 20000.0,
         "employment", False, False, True, "citizen", None,
         "opted_in", "en", "[]", "[]"),
        # #13: Age 65 — elderly boundary (adult → elderly category)
        ("Margaret", "Davis", "1961-06-30", 65, "TX", "Dallas", 1, 2000.0,
         "social_security", False, False, True, "citizen", "medicare",
         "opted_in", "en", "[]", "[]"),
        # #14: Pregnant in non-expansion state (higher pregnant threshold applies)
        ("Tamika", "Williams", "1999-08-20", 27, "GA", "DeKalb", 2, 40000.0,
         "employment", True, False, True, "citizen", None,
         "opted_in", "en", "[]", "[]"),
        # #15: Alaska resident (different FPL table — $19,560 for HH=1)
        ("John", "Whitehorse", "1985-03-12", 41, "AK", "Anchorage", 1, 26000.0,
         "employment", False, False, True, "citizen", None,
         "opted_in", "en", "[]", "[]"),
        # #16: Hawaii, large household size 8 (FPL table boundary)
        ("Leilani", "Kealoha", "1992-07-18", 34, "HI", "Honolulu", 8, 85000.0,
         "employment", False, False, True, "citizen", None,
         "opted_in", "en", "[]", "[]"),
    ]

    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO patients (
                first_name, last_name, date_of_birth, age, state, county,
                household_size, annual_income, income_source,
                is_pregnant, has_disability, is_us_citizen,
                immigration_status, current_insurance,
                consent_status, preferred_language,
                response_history, prior_doc_issues
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, patients)
    conn.commit()


def seed_renewals(conn):
    """Seed 16 renewal scenarios for Phase 2 testing."""
    today = date.today()

    # Helper for communication log entries
    def _sms_log(count, status="no_response"):
        return json.dumps([
            {"type": "sms", "direction": "outbound", "status": status,
             "timestamp": str(today - timedelta(days=60 - i * 15))}
            for i in range(count)
        ])

    renewals = [
        # #1: Standard renewal, patient responds immediately
        (1, str(today + timedelta(days=45)), "NOTIFIED", None, None, "[]",
         "[]", "[]", "[]", "opted_in", "en", None, "completed"),
        # #2: Patient doesn't respond to first 2 messages
        (2, str(today + timedelta(days=30)), "NO_RESPONSE", None, None, "[]",
         "[]", "[]", _sms_log(2), "opted_in", "en", None, "completed"),
        # #3: Patient uploads wrong document
        (3, str(today + timedelta(days=25)), "INVALID_DOC", None, None, "[]",
         '["pay_stub", "birth_certificate"]', "[]", "[]", "opted_in", "en", None, "completed"),
        # #4: Patient's income increased above threshold
        (4, str(today + timedelta(days=40)), "IDENTIFIED", None, None, "[]",
         "[]", "[]", "[]", "opted_in", "en", None, "completed"),
        # #5: Patient moved to non-expansion state
        (5, str(today + timedelta(days=35)), "IDENTIFIED", None, None, "[]",
         "[]", "[]", "[]", "opted_in", "en", None, "completed"),
        # #6: Patient turned 19 (child → adult transition)
        (12, str(today + timedelta(days=50)), "IDENTIFIED", None, None, "[]",
         "[]", "[]", "[]", "opted_in", "en", None, "completed"),
        # #7: Patient became pregnant
        (7, str(today + timedelta(days=40)), "IDENTIFIED", None, None, "[]",
         "[]", "[]", "[]", "opted_in", "en", None, "completed"),
        # #8: Patient opts out of SMS
        (8, str(today + timedelta(days=30)), "NOTIFIED", None, None, "[]",
         "[]", "[]", "[]", "opted_out", "en", None, "completed"),
        # #9: Deadline imminent (<7 days), no response
        (9, str(today + timedelta(days=5)), "NO_RESPONSE", None, None, "[]",
         "[]", "[]", _sms_log(3), "opted_in", "en", None, "completed"),
        # #10: Patient's first renewal ever
        (10, str(today + timedelta(days=55)), "IDENTIFIED", None, None, "[]",
         "[]", "[]", "[]", "opted_in", "en", None, "first_renewal"),
        # #11: Large household (size 8), multiple income sources
        (16, str(today + timedelta(days=45)), "IDENTIFIED", None, None, "[]",
         "[]", "[]", "[]", "opted_in", "en", None, "completed"),
        # #12: Spanish-preferred patient
        (14, str(today + timedelta(days=40)), "IDENTIFIED", None, None, "[]",
         "[]", "[]", "[]", "opted_in", "es", None, "completed"),
        # #13: Document confidence score <0.80
        (6, str(today + timedelta(days=30)), "VALIDATION", None, None, "[]",
         '["ssa_benefit_letter", "utility_bill"]', "[]", "[]", "opted_in", "en", None, "completed"),
        # #14: Patient in Alaska (different FPL table)
        (15, str(today + timedelta(days=50)), "IDENTIFIED", None, None, "[]",
         "[]", "[]", "[]", "opted_in", "en", None, "completed"),
        # #15: All docs valid, ready for submission
        (1, str(today + timedelta(days=20)), "SUBMISSION_READY", None, None, "[]",
         '["pay_stub", "pregnancy_verification"]', '["pay_stub", "pregnancy_verification"]',
         "[]", "opted_in", "en", "nurse_jones", "completed"),
        # #16: Deadline passed, patient never responded
        (2, str(today - timedelta(days=3)), "NO_RESPONSE", None, None, "[]",
         "[]", "[]", _sms_log(4), "opted_in", "en", None, "completed"),
    ]

    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO renewals (
                patient_id, renewal_due_date, current_step,
                risk_score, risk_tier, risk_factors,
                documents_required, documents_received,
                communication_log, consent_status, preferred_language,
                assigned_caseworker, previous_renewal_outcome
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, renewals)
    conn.commit()


def main():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        # Drop existing tables to start fresh (order matters for foreign keys)
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS audit_log")
            cur.execute("DROP TABLE IF EXISTS documents")
            cur.execute("DROP TABLE IF EXISTS renewals")
            cur.execute("DROP TABLE IF EXISTS conversations")
            cur.execute("DROP TABLE IF EXISTS eligibility_history")
            cur.execute("DROP TABLE IF EXISTS patients")
        conn.commit()

        create_tables(conn)
        seed_patients(conn)
        seed_renewals(conn)

        # Verify
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM patients")
            count = cur.fetchone()[0]
            print(f"Database seeded at {DATABASE_URL}")
            print(f"Seeded {count} patients")

            cur.execute(
                "SELECT id, first_name, last_name, age, state, annual_income FROM patients"
            )
            print("\nPatients:")
            for row in cur:
                print(f"  #{row[0]}: {row[1]} {row[2]}, age {row[3]}, {row[4]}, ${row[5]:,.0f}")

            cur.execute("SELECT COUNT(*) FROM renewals")
            renewal_count = cur.fetchone()[0]
            print(f"\nSeeded {renewal_count} renewal scenarios")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
