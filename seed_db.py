"""Create and seed the patients SQLite database with mock data."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "patients.db"


def create_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            date_of_birth TEXT NOT NULL,
            age INTEGER NOT NULL,
            state TEXT NOT NULL,
            county TEXT,
            household_size INTEGER NOT NULL,
            annual_income REAL NOT NULL,
            income_source TEXT,
            is_pregnant INTEGER NOT NULL DEFAULT 0,
            has_disability INTEGER NOT NULL DEFAULT 0,
            is_us_citizen INTEGER NOT NULL DEFAULT 1,
            immigration_status TEXT DEFAULT 'citizen',
            current_insurance TEXT
        );

        CREATE TABLE IF NOT EXISTS eligibility_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            determination_date TEXT NOT NULL,
            eligible INTEGER NOT NULL,
            category TEXT,
            reasoning TEXT,
            FOREIGN KEY (patient_id) REFERENCES patients(id)
        );
    """)


def seed_patients(conn: sqlite3.Connection):
    patients = [
        ("Maria", "Garcia", "1998-03-15", 28, "CA", "Los Angeles", 3, 18000.0,
         "employment", 1, 0, 1, "citizen", "none"),
        ("James", "Wilson", "1981-07-22", 45, "TX", "Harris", 1, 14000.0,
         "employment", 0, 0, 1, "citizen", "none"),
        ("Sarah", "Johnson", "2019-01-10", 7, "FL", "Miami-Dade", 4, 35000.0,
         "employment", 0, 0, 1, "citizen", "none"),
        ("Robert", "Chen", "1956-11-03", 70, "NY", "Kings", 2, 22000.0,
         "social_security", 0, 0, 1, "citizen", "medicare"),
        ("Aisha", "Patel", "1994-05-28", 32, "OH", "Franklin", 5, 42000.0,
         "employment", 0, 0, 1, "citizen", "none"),
        ("David", "Thompson", "1971-09-14", 55, "GA", "Fulton", 1, 8000.0,
         "social_security", 0, 1, 1, "citizen", "none"),
        ("Lisa", "Martinez", "2007-06-20", 19, "WA", "King", 2, 25000.0,
         "employment", 0, 0, 1, "citizen", "none"),
        ("Michael", "Brown", "1986-12-01", 40, "AL", "Jefferson", 3, 12000.0,
         "self-employment", 0, 0, 1, "citizen", "none"),
    ]

    conn.executemany("""
        INSERT INTO patients (
            first_name, last_name, date_of_birth, age, state, county,
            household_size, annual_income, income_source,
            is_pregnant, has_disability, is_us_citizen,
            immigration_status, current_insurance
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, patients)


def main():
    DB_PATH.parent.mkdir(exist_ok=True)

    # Remove existing DB to start fresh
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(str(DB_PATH))
    try:
        create_tables(conn)
        seed_patients(conn)
        conn.commit()

        # Verify
        cursor = conn.execute("SELECT COUNT(*) FROM patients")
        count = cursor.fetchone()[0]
        print(f"Database created at {DB_PATH}")
        print(f"Seeded {count} patients")

        # Show summary
        cursor = conn.execute(
            "SELECT id, first_name, last_name, age, state, annual_income FROM patients"
        )
        print("\nPatients:")
        for row in cursor:
            print(f"  #{row[0]}: {row[1]} {row[2]}, age {row[3]}, {row[4]}, ${row[5]:,.0f}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
