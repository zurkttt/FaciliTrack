# FaciliTrack: User Acceptance Testing (UAT) Kit

User acceptance testing checks that FaciliTrack does what campus users need, in their own hands
(Section 14.2, item 33). This kit contains:

1. A guide for the team running the sessions
2. Test scripts, one set per role
3. A feedback questionnaire
4. A results summary sheet

Print one copy of the scripts for the participant's role, plus the questionnaire.

---

## Part 1: Facilitator guide

**Before each session**

1. Reset the demo data so every participant starts from the same state:
   `python manage.py seed_demo --reset`, then `python manage.py runserver`.
2. Sign in once as `admin` and confirm the home page loads.
3. Give the participant the username for their role. All demo passwords are `Demo@12345`.

| Participant | Username | Scripts |
|---|---|---|
| Facilities / property management staff | `facilities` | F-01 to F-08 |
| Registrar / scheduling staff | `registrar` | R-01 to R-04 |
| Planning / institutional research | `planner` | P-01 to P-06 |
| Department head | `depthead` | D-01 to D-03 |
| Faculty, staff or student user | `viewer` | V-01 to V-05 |
| System administrator | `admin` | A-01 to A-04 |

**During the session**

- Read each step aloud and let the participant do it. Don't guide their mouse.
- Mark each script **Pass**, **Fail** or **Pass with issues**, and write down what they said.
- If something fails, record the severity:
  - **Critical:** the task cannot be completed, or data is wrong.
  - **Major:** the task is completed only with help or a workaround.
  - **Minor:** cosmetic or wording issue.
- Let the participant finish the questionnaire alone.

**Acceptance criteria (proposed)**

- All scripts marked **(critical path)** pass.
- No open Critical issues.
- Average questionnaire score of **4.0 or higher** (out of 5) in every category.

---

## Part 2: Test scripts

Participant: __________________ Role: __________________ Date: __________ Facilitator: __________

### Viewer (faculty, staff or student): sign in as `viewer`

| ID | Steps | Expected result | Pass / Fail | Remarks |
|---|---|---|---|---|
| V-01 (critical path) | Sign in. Look at the home page. | Your upcoming reservation "Thesis defense panel" is listed. There are no risk or approval sections. | | |
| V-02 (critical path) | Open **Reservations**, then the **Find a Room** tab. Pick a weekday next week, 13:00–14:00, 30 attendees. Search. | A list of available rooms appears, smallest suitable rooms first. Rooms with fewer than 30 seats are not listed. | | |
| V-03 (critical path) | Click **Reserve** on one result. Enter a purpose and submit. | The request is saved as **Pending**. The date, time and room are already filled in. | | |
| V-04 | Open **Reservations → New request** and try to book ENG-CL1 next Monday 09:00–10:00. | The request is refused with a message that it overlaps a class. | | |
| V-05 | Try to open `/risks/` and `/reports/` by typing them in the address bar. | "Access denied" is shown for both. | | |

### Facilities staff: sign in as `facilities`

| ID | Steps | Expected result | Pass / Fail | Remarks |
|---|---|---|---|---|
| F-01 (critical path) | On the home page, find **Awaiting your approval**. Open "Faculty meeting". Approve it with a remark. | The status becomes Approved. (The requester, `viewer`, gets an alert; check later in V-01 or by signing in as `viewer`.) | | |
| F-02 (critical path) | Open **Facilities**, then MAIN-201. Change its status to **Under Maintenance**. Then change it back to Available. | The status badge changes each time. A success message appears. | | |
| F-03 | Open **Facilities → Add facility**. Try to add a facility with code `main-101`. | The form refuses it: "A facility with this code already exists." | | |
| F-04 (critical path) | Open **Usage → Attendance sheet**. For one class that has started today, enter a headcount and click **Used**. | The row changes to **Used** with the headcount. | | |
| F-05 | Open **Usage → Room QR codes**. Print preview one page. | One printable QR card per room, each showing the room code. | | |
| F-06 | Open any QR check-in link (or go to `/usage/checkin/LIB-SA1/`). Enter a headcount and **Check in**, then **Check out now**. | "In use now" appears after check-in. Checking out within a minute cancels the check-in. | | |
| F-07 (critical path) | Open **Risks**. Open the **Unavailable facility** risk for LIB-DR1. Add a mitigation action assigned to yourself with a due date. | The action is listed and the risk status changes to **Mitigating**. | | |
| F-08 | Click the bell icon. | Recent alerts are listed. Opening one marks it read and goes to the related page. | | |

### Registrar: sign in as `registrar`

| ID | Steps | Expected result | Pass / Fail | Remarks |
|---|---|---|---|---|
| R-01 (critical path) | Open **Schedules → Import CSV** and download the template. Upload it unchanged. | "Imported 2 schedules." The new classes (EE210-A, ENG102-A) appear in **Schedules**. | | |
| R-02 (critical path) | Edit the template so one row uses facility code `NOPE-1`. Upload it. | "Nothing was imported." The error report names the row and the unknown code. | | |
| R-03 | Open **Schedules → Add schedule**. Try to add a class in ENG-CL1 on Monday 08:00–09:00. | The form refuses it because it overlaps an existing class. | | |
| R-04 | Open the home page and **My actions**. Open the scheduling-conflict risk you own (LIB-DR1). | The risk explains the conflict: an approved thesis defense during the maintenance period. | | |

### Planner: sign in as `planner`

| ID | Steps | Expected result | Pass / Fail | Remarks |
|---|---|---|---|---|
| P-01 (critical path) | Open **Utilization**. Read the four summary numbers and the charts. | The overall utilization is shown, ENG-CL1 is the most used room, and the peak-hours heat map is darkest late morning and early afternoon. | | |
| P-02 | Change **Group by** to Building and **Trend** to Monthly. Apply. | The table and charts regroup by building. | | |
| P-03 (critical path) | Open **Risks**. In the risk matrix, click a red cell that shows a number. | The list filters to exactly that many High-priority risks. | | |
| P-04 | Open the over-demand risk for ENG-CL1. Set status to **Accepted** without a note. Then try again with a note. | Without a note: refused. With a note: saved, and the note appears in History. | | |
| P-05 (critical path) | Open **Reports → Facility utilization**. Download the PDF and the Excel file. | Both files open. The numbers match the on-screen preview. The report states the period and metric definition version. | | |
| P-06 | Open **Admin → Thresholds**. Change the underutilization threshold to 25 with a reason. Save. | Saved. The change and reason appear under "Recent changes." | | |

### Department head: sign in as `depthead`

| ID | Steps | Expected result | Pass / Fail | Remarks |
|---|---|---|---|---|
| D-01 (critical path) | Open **Utilization**. Filter by your department (CCS). | Only CCS-owned rooms are shown. | | |
| D-02 | Open **Risks** and try to change the status of any risk you don't own. | Risks are visible, but there is no edit form (read-only). | | |
| D-03 | Open **Reports → Risk register** and download the CSV. | The file downloads and opens in Excel. | | |

### Administrator: sign in as `admin`

| ID | Steps | Expected result | Pass / Fail | Remarks |
|---|---|---|---|---|
| A-01 (critical path) | Open **Admin → Users → Add user**. Create a user with the Viewer role. | A temporary password is shown once. | | |
| A-02 (critical path) | Sign out, then sign in as the new user with the temporary password. | You are required to choose a new password before seeing any page. | | |
| A-03 | Sign back in as `admin`. Try to deactivate your own account. | No Deactivate button is shown for yourself. | | |
| A-04 | Open **Admin → Audit log**. Find the user you created and the threshold change from P-06 (if run). | Both appear with who, when and what changed. | | |

---

## Part 3: Feedback questionnaire

Name (optional): __________________ Role: __________________ Date: __________

Rate each statement: **1** Strongly disagree · **2** Disagree · **3** Neutral · **4** Agree · **5** Strongly agree.
The categories follow the ISO/IEC 25010 software quality model.

| # | Category | Statement | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|---|
| 1 | Functional suitability | The system lets me complete the tasks my job requires. | | | | | |
| 2 | Functional suitability | The information shown (availability, utilization, risks) is correct as far as I can tell. | | | | | |
| 3 | Functional suitability | Conflict and capacity warnings appear when they should. | | | | | |
| 4 | Performance efficiency | Pages load quickly enough for daily work. | | | | | |
| 5 | Performance efficiency | Searching for an available room is fast. | | | | | |
| 6 | Usability | I could find the features I needed without help. | | | | | |
| 7 | Usability | Labels, messages and error messages are clear. | | | | | |
| 8 | Usability | The system works well on my phone (QR check-in, room search). | | | | | |
| 9 | Reliability | The system behaved consistently and without errors during testing. | | | | | |
| 10 | Security | I only see information appropriate to my role. | | | | | |
| 11 | Security | I trust the system to keep records of who changed what. | | | | | |
| 12 | Overall | The dashboards and reports would help my office make better facility decisions. | | | | | |
| 13 | Overall | I would use this system if the university adopted it. | | | | | |

**What did you like most?**

&nbsp;

**What was confusing or difficult?**

&nbsp;

**What is missing that your office would need?**

&nbsp;

---

## Part 4: Results summary (for the team)

| Role | Participants | Scripts passed | Critical issues | Major issues | Minor issues |
|---|---:|---:|---:|---:|---:|
| Viewer | | / 5 | | | |
| Facilities | | / 8 | | | |
| Registrar | | / 4 | | | |
| Planner | | / 6 | | | |
| Department head | | / 3 | | | |
| Administrator | | / 4 | | | |

| Questionnaire category | Items | Mean score (1–5) | Interpretation |
|---|---|---:|---|
| Functional suitability | 1–3 | | |
| Performance efficiency | 4–5 | | |
| Usability | 6–8 | | |
| Reliability | 9 | | |
| Security | 10–11 | | |
| Overall | 12–13 | | |

Suggested interpretation of means: 4.50–5.00 Excellent · 3.50–4.49 Very good · 2.50–3.49 Good ·
1.50–2.49 Fair · 1.00–1.49 Poor. Use whatever scale your adviser prefers.
