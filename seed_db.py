"""Create and seed the patients PostgreSQL database with mock data."""

import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/medicaid")


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
                current_insurance TEXT
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
        """)
    conn.commit()


def seed_patients(conn):
    patients = [
        ("Maria", "Garcia", "1998-03-15", 28, "CA", "Los Angeles", 3, 18000.0,
         "employment", True, False, True, "citizen", None),
        ("James", "Wilson", "1981-07-22", 45, "TX", "Harris", 1, 14000.0,
         "employment", False, False, True, "citizen", None),
        ("Sarah", "Johnson", "2019-01-10", 7, "FL", "Miami-Dade", 4, 35000.0,
         "employment", False, False, True, "citizen", None),
        ("Robert", "Chen", "1956-11-03", 70, "NY", "Kings", 2, 22000.0,
         "social_security", False, False, True, "citizen", "medicare"),
        ("Aisha", "Patel", "1994-05-28", 32, "OH", "Franklin", 5, 42000.0,
         "employment", False, False, True, "citizen", None),
        ("David", "Thompson", "1971-09-14", 55, "GA", "Fulton", 1, 8000.0,
         "social_security", False, True, True, "citizen", None),
        ("Lisa", "Martinez", "2007-06-20", 19, "WA", "King", 2, 25000.0,
         "employment", False, False, True, "citizen", None),
        ("Michael", "Brown", "1986-12-01", 40, "AL", "Jefferson", 3, 12000.0,
         "self-employment", False, False, True, "citizen", None),
        # --- Border / edge cases ---
        # #9: Income exactly at threshold (CA adult, 138% of $15,650 = $21,597)
        ("Elena", "Ruiz", "1990-04-10", 36, "CA", "San Diego", 1, 21597.0,
         "employment", False, False, True, "citizen", None),
        # #10: Income $1 over threshold (OH adult, 138% of $15,650 = $21,597 → $21,598)
        ("Kevin", "Park", "1988-11-25", 38, "OH", "Cuyahoga", 1, 21598.0,
         "employment", False, False, True, "citizen", None),
        # #11: Non-US citizen — should be ineligible regardless of income
        ("Yuki", "Tanaka", "1995-02-14", 31, "NY", "Queens", 2, 10000.0,
         "employment", False, False, False, "non-immigrant", None),
        # #12: Age 18 — just crossed from child to adult (higher threshold lost)
        ("Jordan", "Lee", "2008-01-05", 18, "FL", "Broward", 3, 20000.0,
         "employment", False, False, True, "citizen", None),
        # #13: Age 65 — elderly boundary (adult → elderly category)
        ("Margaret", "Davis", "1961-06-30", 65, "TX", "Dallas", 1, 2000.0,
         "social_security", False, False, True, "citizen", "medicare"),
        # #14: Pregnant in non-expansion state (higher pregnant threshold applies)
        ("Tamika", "Williams", "1999-08-20", 27, "GA", "DeKalb", 2, 40000.0,
         "employment", True, False, True, "citizen", None),
        # #15: Alaska resident (different FPL table — $19,560 for HH=1)
        ("John", "Whitehorse", "1985-03-12", 41, "AK", "Anchorage", 1, 26000.0,
         "employment", False, False, True, "citizen", None),
        # #16: Hawaii, large household size 8 (FPL table boundary)
        ("Leilani", "Kealoha", "1992-07-18", 34, "HI", "Honolulu", 8, 85000.0,
         "employment", False, False, True, "citizen", None),
    ]

    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO patients (
                first_name, last_name, date_of_birth, age, state, county,
                household_size, annual_income, income_source,
                is_pregnant, has_disability, is_us_citizen,
                immigration_status, current_insurance
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, patients)
    conn.commit()


def main():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        # Drop existing tables to start fresh
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS conversations")
            cur.execute("DROP TABLE IF EXISTS eligibility_history")
            cur.execute("DROP TABLE IF EXISTS patients")
        conn.commit()

        create_tables(conn)
        seed_patients(conn)

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
    finally:
        conn.close()


if __name__ == "__main__":
    main()
