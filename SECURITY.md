# Security Policy

## Reporting Security Vulnerabilities

If you discover a security vulnerability, please email the maintainers instead of using the issue tracker.

**Do not publicly disclose the vulnerability until it has been addressed.**

## Security Considerations

### Default Credentials
- Change the default admin password immediately after first login
- Do not use default credentials in production environments

### Environment Variables
- Always set `SECRET_KEY` to a strong, random value in production
- Use environment variables for sensitive configuration
- Never commit `.env` files to version control (only `.env.example`)

### Database
- The SQLite database file (`baze.db`) contains sensitive user and student data
- Ensure proper file system permissions on the database file
- Regular backups are recommended

### Deployment
- Use HTTPS in production
- Configure proper CORS settings
- Implement rate limiting
- Keep dependencies updated

## Dependency Updates

Run `pip install --upgrade -r requirements.txt` regularly to get security updates.
